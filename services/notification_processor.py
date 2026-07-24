"""
Motor de processamento de regras de notificação WhatsApp (Fase 4 + PRD).

Recursos:
  - Cooldown anti-spam por funcionário + tipo de regra
  - Direito à Desconexão: enfileira mensagens fora do horário de trabalho
  - CLT art. 386: alerta para trabalho de funcionárias mulheres no domingo
  - Relatório de inconsistências do dia anterior
"""
import logging
import os
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from extensions import db
from models import NotificationRule, AlocacaoDiaria, Batida, Funcionario, WhatsappLog, FilaEnvioWhatsapp

_TZ_BR = ZoneInfo('America/Sao_Paulo')
logger = logging.getLogger('notification_processor')

def _get_gestor_celular():
    from services.config_service import get_gestor_celular
    return get_gestor_celular()

# Cooldown padrão: não reenviar o mesmo tipo de alerta para o mesmo funcionário
# dentro de N horas (evita spam a cada ciclo de sync).
COOLDOWN_HORAS = int(os.getenv('NOTIF_COOLDOWN_HORAS', '12'))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render(template: str, func, minutos: int = 0, aloc=None, data_ref: date = None, extra: dict = None) -> str:
    if not template:
        return ''
    
    if data_ref is None:
        data_ref = date.today()

    partes = func.nome.split()
    
    # ── Expansão PRD 2.0: Saldo de Banco de Horas ─────────────────────────────
    saldo_dia = "0.00"
    saldo_acumulado = "0.00"
    try:
        from models import BancoHorasSaldo
        s = BancoHorasSaldo.query.filter_by(funcionario_id=func.id, data=data_ref).first()
        if s:
            saldo_dia = f"{float(s.saldo_dia):.2f}"
            saldo_acumulado = f"{float(s.saldo_acumulado):.2f}"
    except Exception:
        pass

    # ── Expansão PRD 2.0: Interjornada Devida ──────────────────────────────────
    interjornada_devida = "0.00"
    try:
        from services.motor_clt import calcular_gap_interjornada
        from models import Turno
        turno_ref = aloc.turno if aloc else None
        gap = calcular_gap_interjornada(func.id, data_ref, turno_ref)
        if gap and gap < 11.0:
            interjornada_devida = f"{11.0 - gap:.2f}"
    except Exception:
        pass

    subs = {
        # Legado/Padrão
        '{name}':      partes[0] if partes else func.nome,
        '{full_name}': func.nome,
        '{minutes}':   str(minutos),
        '{turno}':     aloc.turno.nome if aloc else '',
        '{inicio}':    aloc.turno.hora_inicio.strftime('%H:%M') if aloc and aloc.turno else '',
        '{fim}':       aloc.turno.hora_fim.strftime('%H:%M') if aloc and aloc.turno else '',
        '{data}':      data_ref.strftime('%d/%m/%Y'),
        '{sexo_art}':  'a' if (func.sexo == 'F') else 'o',
        
        # PRD 2.0 (Tags sugeridas pelo usuário com {{ }})
        '{{name}}':      partes[0] if partes else func.nome,
        '{{full_name}}': func.nome,
        '{{minutes}}':   str(minutos),
        '{{turno}}':     aloc.turno.nome if aloc else '',
        '{{inicio}}':    aloc.turno.hora_inicio.strftime('%H:%M') if aloc and aloc.turno else '',
        '{{fim}}':       aloc.turno.hora_fim.strftime('%H:%M') if aloc and aloc.turno else '',
        '{{data}}':      data_ref.strftime('%d/%m/%Y'),
        '{{saldo_dia}}':       saldo_dia,
        '{{saldo_acumulado}}': saldo_acumulado,
        '{{interjornada_devida}}': interjornada_devida,
    }
    
    for var, val in subs.items():
        template = template.replace(var, val)

    if extra:
        for k, v in extra.items():
            template = template.replace(f'{{{{{k}}}}}', str(v))

    # PRD Antiban Fase 3: Spintax como última etapa — todas as tags já foram
    # substituídas acima, então só resta a sintaxe {opção1|opção2} do próprio
    # admin, se houver.
    from services.spintax import resolver_spintax
    template = resolver_spintax(template)

    return template


def _celular_gestor(func) -> str:
    if func and func.departamento:
        from models import UnidadeLider
        ul = UnidadeLider.query.filter_by(departamento=func.departamento).first()
        if ul and ul.celular_lider:
            return ul.celular_lider
    return _get_gestor_celular()


def _combine(d: date, t) -> datetime:
    return datetime.combine(d, t)


def _parse_hora(hora_str: str, data_ref: date) -> datetime:
    return datetime.strptime(hora_str[:5], '%H:%M').replace(
        year=data_ref.year, month=data_ref.month, day=data_ref.day
    )


# ── Cooldown anti-spam ─────────────────────────────────────────────────────────

def _em_cooldown(func_id: str, condition_type: str, data_ref: date = None) -> bool:
    """Retorna True se já foi enviada mensagem do mesmo tipo nas últimas COOLDOWN_HORAS horas."""
    limite = datetime.now(_TZ_BR) - timedelta(hours=COOLDOWN_HORAS)
    
    # Busca por tipo_regra e data_referencia (novos) ou tipo (legado)
    query = WhatsappLog.query.filter(
        WhatsappLog.funcionario_id == func_id,
        WhatsappLog.status == 'enviado',
        WhatsappLog.criado_em >= limite
    )
    
    if data_ref:
        query = query.filter(
            db.or_(
                db.and_(
                    WhatsappLog.tipo_regra == condition_type,
                    WhatsappLog.data_referencia == data_ref
                ),
                WhatsappLog.tipo == f'regra_{condition_type}'
            )
        )
    else:
        query = query.filter(
            db.or_(
                WhatsappLog.tipo_regra == condition_type,
                WhatsappLog.tipo == f'regra_{condition_type}'
            )
        )
        
    return query.first() is not None


# ── Direito à Desconexão ───────────────────────────────────────────────────────

def _proximo_inicio_turno(aloc) -> datetime | None:
    """Calcula o próximo início de turno do funcionário (amanhã)."""
    if not aloc or not aloc.turno:
        return None
    amanha = date.today() + timedelta(days=1)
    return datetime.combine(amanha, aloc.turno.hora_inicio)


def _enfileirar(regra: NotificationRule, celular: str, mensagem: str,
                func_id: str | None, aloc, tipo: str, 
                tipo_regra: str = None, data_ref: date = None) -> None:
    """Enfileira mensagem para envio no próximo início de turno."""
    enviar_apos = _proximo_inicio_turno(aloc)
    item = FilaEnvioWhatsapp(
        regra_id=regra.id,
        funcionario_id=func_id,
        celular=celular,
        mensagem=mensagem,
        tipo=tipo,
        tipo_regra=tipo_regra,
        data_referencia=data_ref,
        tipo_msg='texto',
        prioridade=10,
        enviar_apos=enviar_apos,
        status='pendente',
    )
    db.session.add(item)


def _fora_do_expediente(aloc) -> bool:
    """True se o horário atual está fora do turno da alocação OU em horário de silêncio (22h-07h)."""
    agora = datetime.now(_TZ_BR).time()

    # Janela de silêncio absoluta para compliance (22h às 07h)
    if agora >= datetime.strptime('22:00', '%H:%M').time() or \
       agora <= datetime.strptime('07:00', '%H:%M').time():
        return True

    if not aloc or not aloc.turno:
        return False

    return not (aloc.turno.hora_inicio <= agora <= aloc.turno.hora_fim)


# ── Relatório de Inconsistências ───────────────────────────────────────────────

def _formatar_batidas(lista_batidas: list) -> str:
    """Retorna string com horários. Ex: '08:02 · 12:01 · 13:05 · 17:58'"""
    horas = []
    for b in sorted(lista_batidas, key=lambda x: x.hora):
        hora_str = b.hora if isinstance(b.hora, str) else b.hora.strftime('%H:%M')
        horas.append(hora_str[:5])
    return ' · '.join(horas) if horas else '—'


def _gerar_relatorio_inconsistencias(data_ref: date) -> str:
    """Monta texto resumido GLOBAL das inconsistências do dia data_ref."""
    from blueprints.inconsistencias import _analisar_dia

    batidas = (
        Batida.query
        .filter_by(data=data_ref)
        .join(Funcionario, Batida.funcionario_id == Funcionario.id)
        .filter(Funcionario.ativo == True)
        .all()
    )

    alocacoes = (
        AlocacaoDiaria.query
        .filter_by(data=data_ref)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )

    batidas_por_fid = {}
    for b in batidas:
        batidas_por_fid.setdefault(b.funcionario_id, []).append(b)

    # {nome: {'tipos': set, 'batidas': str, 'turno': str}}
    por_func = {}
    from services.feriados_service import is_feriado
    from models import UnidadeLider
    
    # Cache de unidades por departamento
    unidades_map = {u.departamento: u for u in UnidadeLider.query.all()}

    for fid, lista in batidas_por_fid.items():
        if lista:
            func = lista[0].funcionario
            probs = _analisar_dia(func, data_ref, lista)
            if probs:
                aloc = AlocacaoDiaria.query.filter_by(funcionario_id=fid, data=data_ref).first()
                turno_str = ''
                if aloc and aloc.turno:
                    turno_str = f'{aloc.turno.hora_inicio.strftime("%H:%M")}–{aloc.turno.hora_fim.strftime("%H:%M")}'
                por_func[func.nome] = {
                    'tipos': {p['tipo'] for p in probs},
                    'batidas': _formatar_batidas(lista),
                    'turno': turno_str,
                }

    ausentes = []
    for aloc in alocacoes:
        func = aloc.funcionario
        if func and func.id not in batidas_por_fid:
            # Verifica se é feriado para a unidade do funcionário
            ul = unidades_map.get(func.departamento)
            ibge = getattr(ul, 'cidade_ibge', None) if ul else None
            uf   = getattr(ul, 'empresa_uf', None) if ul else None
            
            if is_feriado(data_ref, ibge, uf):
                continue # Pula se for feriado na localidade dele

            turno_str = ''
            if aloc.turno:
                turno_str = f'{aloc.turno.hora_inicio.strftime("%H:%M")}–{aloc.turno.hora_fim.strftime("%H:%M")}'
            ausentes.append({'nome': func.nome, 'turno': turno_str})

    from services.spintax import resolver_spintax
    # PRD Antiban Fase 3: INCONSISTENCY_REPORT monta o texto diretamente (não
    # passa por _render/templates), então o Spintax é aplicado aqui, só no
    # cabeçalho e nos títulos de seção fixos — não no conteúdo dinâmico.
    linhas = [resolver_spintax(
        '{📋 Inconsistências|📋 Resumo de inconsistências|📋 Relatório de pendências} — '
        + data_ref.strftime("%d/%m/%Y")
    )]

    if por_func:
        linhas.append(resolver_spintax(
            f'\n{{⚠️ Batidas inconsistentes|⚠️ Marcações irregulares|⚠️ Pontos com problema}} ({len(por_func)}):'
        ))
        for nome in sorted(por_func):
            d = por_func[nome]
            tipos = ', '.join(sorted(d['tipos']))
            linha = f'  • {nome}: {tipos}'
            if d['turno']:
                linha += f'\n    🕐 Turno: {d["turno"]}'
            linha += f'\n    👆 Batidas: {d["batidas"]}'
            linhas.append(linha)

    if ausentes:
        linhas.append(resolver_spintax(
            f'\n{{🚫 Ausências|🚫 Sem ponto registrado|🚫 Faltas do dia}} ({len(ausentes)}):'
        ))
        for a in sorted(ausentes, key=lambda x: x['nome']):
            linha = f'  • {a["nome"]}'
            if a['turno']:
                linha += f' (turno {a["turno"]})'
            linhas.append(linha)

    if not por_func and not ausentes:
        linhas.append(resolver_spintax(
            '\n{✅ Nenhuma inconsistência encontrada.|✅ Tudo certo por aqui hoje.|✅ Nenhuma pendência identificada.}'
        ))

    return '\n'.join(linhas)


def _gerar_relatorio_por_departamento(data_ref: date) -> dict:
    """
    Retorna {departamento: texto_relatorio} apenas para departamentos
    que possuem inconsistências ou ausências no dia data_ref.
    """
    from blueprints.inconsistencias import _analisar_dia
    from models import UnidadeLider
    
    # Cache de unidades por departamento
    unidades_map = {u.departamento: u for u in UnidadeLider.query.all()}
    
    batidas = (
        Batida.query
        .filter(Batida.data == data_ref)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )
    alocacoes = (
        AlocacaoDiaria.query
        .filter_by(data=data_ref)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )
    
    batidas_por_fid = {}
    for b in batidas:
        batidas_por_fid.setdefault(b.funcionario_id, []).append(b)

    # {dept: {'inconsistentes': {nome: {tipos, batidas, turno}}, 'ausentes': [{nome, turno}]}}
    por_dept = {}

    for fid, lista in batidas_por_fid.items():
        if lista:
            func = lista[0].funcionario
            dept = func.departamento or 'Sem Departamento'
            probs = _analisar_dia(func, data_ref, lista)
            if probs:
                aloc = AlocacaoDiaria.query.filter_by(funcionario_id=fid, data=data_ref).first()
                turno_str = ''
                if aloc and aloc.turno:
                    turno_str = f'{aloc.turno.hora_inicio.strftime("%H:%M")}–{aloc.turno.hora_fim.strftime("%H:%M")}'
                d = por_dept.setdefault(dept, {'inconsistentes': {}, 'ausentes': []})
                d['inconsistentes'][func.nome] = {
                    'tipos': {p['tipo'] for p in probs},
                    'batidas': _formatar_batidas(lista),
                    'turno': turno_str,
                }

    for aloc in alocacoes:
        func = aloc.funcionario
        if func and func.id not in batidas_por_fid:
            # Verifica feriado para ausência
            ul = unidades_map.get(func.departamento)
            ibge = getattr(ul, 'cidade_ibge', None) if ul else None
            uf   = getattr(ul, 'empresa_uf', None) if ul else None
            from services.feriados_service import is_feriado
            if is_feriado(data_ref, ibge, uf):
                continue

            dept = func.departamento or 'Sem Departamento'
            turno_str = ''
            if aloc.turno:
                turno_str = f'{aloc.turno.hora_inicio.strftime("%H:%M")}–{aloc.turno.hora_fim.strftime("%H:%M")}'
            d = por_dept.setdefault(dept, {'inconsistentes': {}, 'ausentes': []})
            d['ausentes'].append({'nome': func.nome, 'turno': turno_str})

    # Gera texto por departamento
    from services.spintax import resolver_spintax
    result = {}
    data_str = data_ref.strftime('%d/%m/%Y')
    for dept, dados in por_dept.items():
        titulo = resolver_spintax('{📋 Inconsistências|📋 Resumo de inconsistências|📋 Relatório de pendências}')
        linhas = [f'{titulo} — {dept} — {data_str}']
        if dados['inconsistentes']:
            linhas.append(resolver_spintax(
                f'\n{{⚠️ Batidas inconsistentes|⚠️ Marcações irregulares|⚠️ Pontos com problema}} ({len(dados["inconsistentes"])}):'
            ))
            for nome in sorted(dados['inconsistentes']):
                info = dados['inconsistentes'][nome]
                tipos = ', '.join(sorted(info['tipos']))
                linha = f'  • {nome}: {tipos}'
                if info['turno']:
                    linha += f'\n    🕐 Turno: {info["turno"]}'
                linha += f'\n    👆 Batidas: {info["batidas"]}'
                linhas.append(linha)
        if dados['ausentes']:
            linhas.append(resolver_spintax(
                f'\n{{🚫 Ausências|🚫 Sem ponto registrado|🚫 Faltas do dia}} ({len(dados["ausentes"])}):'
            ))
            for a in sorted(dados['ausentes'], key=lambda x: x['nome']):
                linha = f'  • {a["nome"]}'
                if a['turno']:
                    linha += f' (turno {a["turno"]})'
                linhas.append(linha)
        result[dept] = '\n'.join(linhas)
    return result


def _normalizar_celular(numero: str) -> str:
    """Remove caracteres não numéricos e garante DDI 55 (Brasil)."""
    import re
    num = re.sub(r'\D', '', numero)
    if len(num) >= 10 and not num.startswith('55'):
        num = '55' + num
    return num


def _enviar_relatorio(regra: NotificationRule, texto_global: str) -> int:
    """
    Envia relatório de inconsistências respeitando os destinatários da regra
    (correção: antes desta versão, os líderes de unidade recebiam o relatório
    incondicionalmente, ignorando dest_manager/dest_rh/dest_custom):
    - dest_manager: cada líder de unidade (relatório do próprio departamento) + gestor global
    - dest_rh: gestor global (não há celular de RH dedicado hoje, reaproveita o mesmo campo)
    - dest_custom: número customizado da regra (relatório completo)
    Cada envio é isolado em try/except: falha num depto não interrompe os demais.
    """
    from services.whatsapp_bot import enviar_texto
    from models import UnidadeLider
    enviados = 0

    # Data de ontem no fuso do Brasil (evita divergência UTC vs BRT na virada do dia)
    ontem = (datetime.now(_TZ_BR).date() - timedelta(days=1))

    try:
        relatorios_dept = _gerar_relatorio_por_departamento(ontem)
    except Exception as e:
        logger.error(f'[enviar_relatorio] Erro ao gerar relatório por departamento: {e}')
        relatorios_dept = {}

    if regra.dest_manager:
        # Extrai dados em memória ANTES dos envios (commits do enviar_texto expiram a ORM)
        unidades = UnidadeLider.query.filter(UnidadeLider.celular_lider.isnot(None)).all()
        alvos = []
        for u in unidades:
            if u.celular_lider:
                alvos.append({
                    'celular': _normalizar_celular(u.celular_lider),
                    'dept': u.departamento,
                })

        for alvo in alvos:
            dept = alvo['dept']
            texto_dept = relatorios_dept.get(dept) or (
                f'📋 Inconsistências — {dept} — {ontem.strftime("%d/%m/%Y")}\n\n✅ Nenhuma inconsistência encontrada.'
            )
            try:
                if enviar_texto(celular=alvo['celular'], mensagem=texto_dept, tipo='relatorio'):
                    enviados += 1
            except Exception as e:
                logger.error(f'[enviar_relatorio] Falha ao enviar para depto "{dept}" ({alvo["celular"]}): {e}')

        # Relatório global para o gestor geral
        if _get_gestor_celular():
            cel_gestor = _normalizar_celular(_get_gestor_celular())
            try:
                if enviar_texto(celular=cel_gestor, mensagem=texto_global, tipo='relatorio'):
                    enviados += 1
            except Exception as e:
                logger.error(f'[enviar_relatorio] Falha ao enviar relatório global para {cel_gestor}: {e}')

    if regra.dest_rh and _get_gestor_celular():
        cel_gestor = _normalizar_celular(_get_gestor_celular())
        try:
            if enviar_texto(celular=cel_gestor, mensagem=texto_global, tipo='relatorio'):
                enviados += 1
        except Exception as e:
            logger.error(f'[enviar_relatorio] Falha ao enviar relatório (RH) para {cel_gestor}: {e}')

    if getattr(regra, 'dest_custom', False) and getattr(regra, 'custom_phone', None):
        num = _normalizar_celular(regra.custom_phone)
        try:
            if enviar_texto(celular=num, mensagem=texto_global, tipo='relatorio'):
                enviados += 1
        except Exception as e:
            logger.error(f'[enviar_relatorio] Falha ao enviar relatório (custom) para {num}: {e}')

    return enviados


# ── CLT art. 386 — Descanso dominical feminino ────────────────────────────────

def _checar_descanso_domingo_f(func: Funcionario, data_ref: date) -> bool:
    """Retorna True se funcionária (sexo=F) está alocada no domingo SEM compensação
    na semana (segunda a sábado da mesma semana sem folga registrada).
    Regra simplificada: se ela tem alocação no domingo E não tem nenhum dia sem
    alocação na semana (seg-sáb), configura violação.
    """
    if func.sexo != 'F':
        return False
    if data_ref.weekday() != 6:  # 6 = domingo
        return False
    # Verifica se há alguma folga na semana (seg-sáb antes do domingo)
    seg = data_ref - timedelta(days=6)
    for delta in range(6):
        dia = seg + timedelta(days=delta)
        tem_aloc = AlocacaoDiaria.query.filter_by(
            funcionario_id=func.id, data=dia
        ).first()
        if not tem_aloc:
            return False  # existe folga na semana → não é violação
    return True  # trabalhou todos os dias seg-sáb + dom → violação CLT 386


# ── Checadores de condição ─────────────────────────────────────────────────────

def _checar_atraso(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if not batidas:
        return False, 0
    primeira = _parse_hora(batidas[0].hora, data_ref)
    ini_turno = _combine(data_ref, aloc.turno.hora_inicio)
    diff = (primeira - ini_turno).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_hora_extra(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if len(batidas) < 2:
        return False, 0
    ultima = _parse_hora(batidas[-1].hora, data_ref)
    fim_turno = _combine(data_ref, aloc.turno.hora_fim)
    diff = (ultima - fim_turno).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_antecipacao(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if len(batidas) < 2:
        return False, 0
    ultima = _parse_hora(batidas[-1].hora, data_ref)
    fim_turno = _combine(data_ref, aloc.turno.hora_fim)
    diff = (fim_turno - ultima).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_ausencia(func_id, data_ref: date) -> bool:
    return Batida.query.filter_by(funcionario_id=func_id, data=data_ref).count() == 0


# ── Escala Semanal ─────────────────────────────────────────────────────────────

def _montar_escala(func, data_ref: date) -> str:
    """Monta texto da escala dos próximos 7 dias a partir de data_ref."""
    DIAS_PT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    linhas = [f'📅 Olá, {func.nome.split()[0]}! Sua escala da semana:']
    for delta in range(7):
        dia = data_ref + timedelta(days=delta)
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func.id, data=dia).first()
        dia_str = f'{DIAS_PT[dia.weekday()]} {dia.strftime("%d/%m")}'
        if aloc and aloc.turno:
            linhas.append(
                f'  {dia_str}: {aloc.turno.hora_inicio.strftime("%H:%M")}–{aloc.turno.hora_fim.strftime("%H:%M")}'
            )
        else:
            linhas.append(f'  {dia_str}: Folga 🏖️')
    return '\n'.join(linhas)


def _checar_pre_checkin(func, data_ref: date) -> bool:
    """True se falta 45-75 min para o turno de hoje e pre_checkin ainda não confirmado."""
    if data_ref != date.today():
        return False
    aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func.id, data=data_ref).first()
    if not aloc or not aloc.turno:
        return False
    if getattr(aloc, 'pre_checkin', False):
        return False
    inicio = datetime.combine(data_ref, aloc.turno.hora_inicio, tzinfo=_TZ_BR)
    diff_min = (inicio - datetime.now(_TZ_BR)).total_seconds() / 60
    return 45 <= diff_min <= 75


# ── Envio (com cooldown + Direito à Desconexão) ────────────────────────────────

def _enviar(regra: NotificationRule, func, minutos: int, aloc, data_ref: date, extra: dict = None) -> int:
    from services.whatsapp_bot import enviar_texto, enviar_botoes, enviar_msg
    enviados = 0
    fora = regra.only_working_hours and _fora_do_expediente(aloc)

    tipo_msg = f'regra_{regra.condition_type}'

    if regra.dest_employee and func.celular:
        msg = _render(regra.template_employee or '', func, minutos, aloc, data_ref, extra=extra)
        if msg:
            if fora and not regra.send_immediately:
                _enfileirar(regra, func.celular, msg, func.id, aloc, tipo_msg,
                           tipo_regra=regra.condition_type, data_ref=data_ref)
                enviados += 1
            elif regra.condition_type == 'PRE_CHECKIN' and not getattr(regra, 'template_employee_tipo', None):
                # Usa botões interativos padrão para check-in prévio
                import json as _j
                default_interativo = _j.dumps({'botoes': [
                    {'id': 'checkin_sim', 'title': '👍 Sim, confirmo'},
                    {'id': 'checkin_nao', 'title': '👎 Não poderei ir'},
                ]})
                ok = enviar_msg(
                    celular=func.celular, texto=msg,
                    tipo_msg='botoes', interativo_json=default_interativo,
                    func_id=func.id, tipo='regra',
                    tipo_regra=regra.condition_type, data_ref=data_ref,
                )
                if ok:
                    enviados += 1
            elif enviar_msg(
                celular=func.celular, texto=msg,
                tipo_msg=getattr(regra, 'template_employee_tipo', None) or 'texto',
                interativo_json=getattr(regra, 'template_employee_interativo', None),
                func_id=func.id, tipo='regra',
                tipo_regra=regra.condition_type, data_ref=data_ref,
            ):
                enviados += 1

    if regra.dest_manager:
        cel = _celular_gestor(func)
        if cel:
            msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref, extra=extra)
            if msg:
                if fora and not regra.send_immediately:
                    _enfileirar(regra, cel, msg, func.id, aloc, tipo_msg,
                               tipo_regra=regra.condition_type, data_ref=data_ref)
                    enviados += 1
                elif enviar_msg(
                    celular=cel, texto=msg,
                    tipo_msg=getattr(regra, 'template_manager_tipo', None) or 'texto',
                    interativo_json=getattr(regra, 'template_manager_interativo', None),
                    func_id=func.id, tipo='regra',
                    tipo_regra=regra.condition_type, data_ref=data_ref,
                ):
                    enviados += 1

    if regra.dest_rh and _get_gestor_celular():
        msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref, extra=extra)
        if msg:
            if fora and not regra.send_immediately:
                _enfileirar(regra, _get_gestor_celular(), msg, func.id, aloc, tipo_msg,
                           tipo_regra=regra.condition_type, data_ref=data_ref)
                enviados += 1
            elif enviar_texto(celular=_get_gestor_celular(), mensagem=msg,
                              func_id=func.id, tipo='regra',
                              tipo_regra=regra.condition_type, data_ref=data_ref):
                enviados += 1

    if getattr(regra, 'dest_custom', False) and getattr(regra, 'custom_phone', None):
        num = re.sub(r'\D', '', regra.custom_phone)
        if len(num) >= 10:
            if not num.startswith('55'):
                num = '55' + num
            msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref, extra=extra)
            if msg:
                if fora and not regra.send_immediately:
                    _enfileirar(regra, num, msg, func.id, aloc, tipo_msg,
                               tipo_regra=regra.condition_type, data_ref=data_ref)
                    enviados += 1
                elif enviar_texto(celular=num, mensagem=msg,
                                  func_id=func.id, tipo='regra',
                                  tipo_regra=regra.condition_type, data_ref=data_ref):
                    enviados += 1

    return enviados


# ── Fila (Direito à Desconexão) ────────────────────────────────────────────────
# PRD Antiban Fase 1: o despacho da fila (incluindo retry com backoff exponencial)
# foi absorvido por services/envio_dispatcher.py + services/whatsapp_bot.py::_processar_item,
# chamado a cada ~5s pelo APScheduler (services/auto_sync.py). _enfileirar() acima
# só precisa calcular enviar_apos; o dispatcher genérico cuida do resto.


# ── Processador principal ──────────────────────────────────────────────────────

def processar_regras_evento(trigger_type: str, data_ref: date = None) -> dict:
    """
    Avalia todas as regras ativas para o trigger dado.
    Chamado após sync de batidas ou manualmente.
    """
    if data_ref is None:
        data_ref = date.today()

    regras = NotificationRule.query.filter_by(ativo=True, trigger_type=trigger_type).all()
    if not regras:
        return {'regras': 0, 'mensagens': 0}

    alocacoes = (
        AlocacaoDiaria.query
        .filter_by(data=data_ref)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )

    total = 0

    for regra in regras:
        enviados_regra = 0

        # Relatório de inconsistências: lógica própria, usa o dia anterior
        if regra.condition_type == 'INCONSISTENCY_REPORT':
            ontem = data_ref - timedelta(days=1)
            relatorio = _gerar_relatorio_inconsistencias(ontem)
            enviados_regra = _enviar_relatorio(regra, relatorio)
            if enviados_regra > 0:
                regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + enviados_regra
                regra.ultima_execucao = datetime.now(_TZ_BR)
            total += enviados_regra
            continue

        for aloc in alocacoes:
            func = aloc.funcionario
            if not func:
                continue

            # Janela de expediente: só verifica, não bloqueia (bloqueio ocorre no _enviar via fila)
            if regra.only_working_hours and not regra.send_immediately:
                pass  # _enviar() cuidará de enfileirar se fora do horário

            threshold = regra.threshold_minutes or 15
            matched, minutos = False, 0

            extra_ctx = None

            if regra.condition_type == 'LATE_ENTRY':
                matched, minutos = _checar_atraso(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'OVERTIME':
                matched, minutos = _checar_hora_extra(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'EARLY_LEAVE':
                matched, minutos = _checar_antecipacao(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'ABSENCE':
                matched = _checar_ausencia(func.id, data_ref)
            elif regra.condition_type == 'INTERJORNADA':
                from services.motor_clt import validar_interjornada
                if validar_interjornada(func.id, data_ref, aloc.turno):
                    matched = True
            elif regra.condition_type == 'DESCANSO_DOMINGO_F':
                matched = _checar_descanso_domingo_f(func, data_ref)
            elif regra.condition_type == 'ESCALA_ENVIO':
                escala_txt = _montar_escala(func, data_ref)
                if escala_txt:
                    matched = True
                    extra_ctx = {'escala': escala_txt}
            elif regra.condition_type == 'PRE_CHECKIN':
                matched = _checar_pre_checkin(func, data_ref)
            elif regra.condition_type == 'DAILY_ABSENCE':
                matched = _checar_ausencia(func.id, data_ref)

            if not matched:
                continue

            # Cooldown: evita reenviar o mesmo tipo de alerta no mesmo período
            if _em_cooldown(func.id, regra.condition_type, data_ref):
                continue

            enviados_regra += _enviar(regra, func, minutos, aloc, data_ref, extra=extra_ctx)

        if enviados_regra > 0:
            regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + enviados_regra
            regra.ultima_execucao = datetime.now(_TZ_BR)
        total += enviados_regra

    db.session.commit()
    return {'regras': len(regras), 'mensagens': total}


def processar_regras_agendadas() -> dict:
    """
    Verifica regras DAILY, WEEKLY e EVENT_ABSENCE para a hora/dia atual,
    e EVENT_HOURLY sempre (sem filtro de hora).
    Chamado a cada hora via Celery beat.
    """
    agora = datetime.now(_TZ_BR)
    hora_atual = agora.hour
    dia_atual  = agora.weekday()

    # Regras baseadas em hora configurável
    regras_hora = NotificationRule.query.filter(
        NotificationRule.ativo == True,
        NotificationRule.trigger_type.in_(['DAILY', 'WEEKLY', 'EVENT_ABSENCE']),
        NotificationRule.trigger_hour == hora_atual,
    ).all()

    total = 0
    processed_types = set()
    for regra in regras_hora:
        if regra.trigger_type == 'WEEKLY' and regra.trigger_weekday != dia_atual:
            continue
        if regra.trigger_type not in processed_types:
            result = processar_regras_evento(regra.trigger_type)
            total += result.get('mensagens', 0)
            processed_types.add(regra.trigger_type)

    # EVENT_HOURLY: sempre executa (verificação contínua, sem filtro de hora)
    result_hourly = processar_regras_evento('EVENT_HOURLY')
    total += result_hourly.get('mensagens', 0)

    return {'total': total}
