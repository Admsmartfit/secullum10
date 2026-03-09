"""
Motor de processamento de regras de notificação WhatsApp (Fase 4 + PRD).

Recursos:
  - Cooldown anti-spam por funcionário + tipo de regra
  - Direito à Desconexão: enfileira mensagens fora do horário de trabalho
  - CLT art. 386: alerta para trabalho de funcionárias mulheres no domingo
  - Relatório de inconsistências do dia anterior
"""
import os
from datetime import datetime, date, timedelta

from extensions import db
from models import NotificationRule, AlocacaoDiaria, Batida, Funcionario, WhatsappLog, NotificacaoFila

GESTOR_CELULAR = os.getenv('GESTOR_CELULAR', '')

# Cooldown padrão: não reenviar o mesmo tipo de alerta para o mesmo funcionário
# dentro de N horas (evita spam a cada ciclo de sync).
COOLDOWN_HORAS = int(os.getenv('NOTIF_COOLDOWN_HORAS', '12'))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render(template: str, func, minutos: int = 0, aloc=None, data_ref=None) -> str:
    if not template:
        return ''
    partes = func.nome.split()
    subs = {
        '{name}':      partes[0] if partes else func.nome,
        '{full_name}': func.nome,
        '{minutes}':   str(minutos),
        '{turno}':     aloc.turno.nome if aloc else '',
        '{inicio}':    aloc.turno.hora_inicio.strftime('%H:%M') if aloc else '',
        '{fim}':       aloc.turno.hora_fim.strftime('%H:%M') if aloc else '',
        '{data}':      (data_ref or date.today()).strftime('%d/%m/%Y'),
        '{sexo_art}':  'a' if (func.sexo == 'F') else 'o',
    }
    for var, val in subs.items():
        template = template.replace(var, val)
    return template


def _celular_gestor(func) -> str:
    if func and func.departamento:
        from models import UnidadeLider
        ul = UnidadeLider.query.filter_by(departamento=func.departamento).first()
        if ul and ul.celular_lider:
            return ul.celular_lider
    return GESTOR_CELULAR


def _combine(d: date, t) -> datetime:
    return datetime.combine(d, t)


def _parse_hora(hora_str: str, data_ref: date) -> datetime:
    return datetime.strptime(hora_str[:5], '%H:%M').replace(
        year=data_ref.year, month=data_ref.month, day=data_ref.day
    )


# ── Cooldown anti-spam ─────────────────────────────────────────────────────────

def _em_cooldown(func_id: str, condition_type: str, data_ref: date = None) -> bool:
    """Retorna True se já foi enviada mensagem do mesmo tipo nas últimas COOLDOWN_HORAS horas."""
    limite = datetime.utcnow() - timedelta(hours=COOLDOWN_HORAS)
    
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
    item = NotificacaoFila(
        regra_id=regra.id,
        funcionario_id=func_id,
        celular=celular,
        mensagem=mensagem,
        tipo=tipo,
        tipo_regra=tipo_regra,    # Supondo que NotificacaoFila também possa precisar disso
        enviar_apos=enviar_apos,
        status='pendente',
    )
    db.session.add(item)


def _fora_do_expediente(aloc) -> bool:
    """True se o horário atual está fora do turno da alocação."""
    if not aloc or not aloc.turno:
        return False
    agora = datetime.now().time()
    return not (aloc.turno.hora_inicio <= agora <= aloc.turno.hora_fim)


# ── Relatório de Inconsistências ───────────────────────────────────────────────

def _gerar_relatorio_inconsistencias(data_ref: date) -> str:
    """Monta texto resumido das inconsistências do dia data_ref."""
    batidas_inc = (
        Batida.query
        .filter_by(data=data_ref, inconsistente=True)
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
    ausentes = []
    for aloc in alocacoes:
        if aloc.funcionario and Batida.query.filter_by(
            funcionario_id=aloc.funcionario_id, data=data_ref
        ).count() == 0:
            ausentes.append(aloc.funcionario.nome)

    linhas = [f'📋 Inconsistências — {data_ref.strftime("%d/%m/%Y")}']

    if batidas_inc:
        por_func = {}
        for b in batidas_inc:
            nome = b.funcionario.nome if b.funcionario else str(b.funcionario_id)
            por_func.setdefault(nome, set()).add(b.tipo_inconsistencia or 'erro')
        linhas.append(f'\n⚠️ Batidas inconsistentes ({len(batidas_inc)}):')
        for nome in sorted(por_func):
            linhas.append(f'  • {nome}: {", ".join(sorted(por_func[nome]))}')

    if ausentes:
        linhas.append(f'\n🚫 Ausências ({len(ausentes)}):')
        for nome in sorted(ausentes):
            linhas.append(f'  • {nome}')

    if not batidas_inc and not ausentes:
        linhas.append('\n✅ Nenhuma inconsistência encontrada.')

    return '\n'.join(linhas)


def _enviar_relatorio(regra: NotificationRule, texto: str) -> int:
    """Envia o texto do relatório para gestor e/ou RH."""
    from services.whatsapp_bot import enviar_texto
    enviados = 0
    if regra.dest_manager and GESTOR_CELULAR:
        if enviar_texto(celular=GESTOR_CELULAR, mensagem=texto, tipo='relatorio'):
            enviados += 1
    if regra.dest_rh and GESTOR_CELULAR:
        pass  # reutiliza o mesmo número global quando não há celular separado
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


# ── Envio (com cooldown + Direito à Desconexão) ────────────────────────────────

def _enviar(regra: NotificationRule, func, minutos: int, aloc, data_ref: date) -> int:
    from services.whatsapp_bot import enviar_texto
    enviados = 0
    fora = regra.only_working_hours and _fora_do_expediente(aloc)

    tipo_msg = f'regra_{regra.condition_type}'

    if regra.dest_employee and func.celular:
        msg = _render(regra.template_employee or '', func, minutos, aloc, data_ref)
        if msg:
            if fora and not regra.send_immediately:
                _enfileirar(regra, func.celular, msg, func.id, aloc, tipo_msg, 
                           tipo_regra=regra.condition_type, data_ref=data_ref)
                enviados += 1
            elif enviar_texto(celular=func.celular, mensagem=msg,
                              func_id=func.id, tipo='regra', 
                              tipo_regra=regra.condition_type, data_ref=data_ref):
                enviados += 1

    if regra.dest_manager:
        cel = _celular_gestor(func)
        if cel:
            msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref)
            if msg:
                if fora and not regra.send_immediately:
                    _enfileirar(regra, cel, msg, func.id, aloc, tipo_msg,
                               tipo_regra=regra.condition_type, data_ref=data_ref)
                    enviados += 1
                elif enviar_texto(celular=cel, mensagem=msg,
                                  func_id=func.id, tipo='regra',
                                  tipo_regra=regra.condition_type, data_ref=data_ref):
                    enviados += 1

    if regra.dest_rh and GESTOR_CELULAR:
        msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref)
        if msg:
            if fora and not regra.send_immediately:
                _enfileirar(regra, GESTOR_CELULAR, msg, func.id, aloc, tipo_msg,
                           tipo_regra=regra.condition_type, data_ref=data_ref)
                enviados += 1
            elif enviar_texto(celular=GESTOR_CELULAR, mensagem=msg,
                              func_id=func.id, tipo='regra',
                              tipo_regra=regra.condition_type, data_ref=data_ref):
                enviados += 1

    return enviados


# ── Processador da fila (Direito à Desconexão) ────────────────────────────────

def processar_fila_notificacoes() -> dict:
    """
    Despacha mensagens enfileiradas cujo enviar_apos já passou.
    Chamado a cada hora via Celery beat.
    """
    from services.whatsapp_bot import enviar_texto
    agora = datetime.utcnow()
    pendentes = (
        NotificacaoFila.query
        .filter(
            NotificacaoFila.status == 'pendente',
            db.or_(
                NotificacaoFila.enviar_apos.is_(None),
                NotificacaoFila.enviar_apos <= agora,
            ),
        )
        .all()
    )
    enviados = 0
    erros = 0
    for item in pendentes:
        item.tentativas = (item.tentativas or 0) + 1
        ok = enviar_texto(
            celular=item.celular,
            mensagem=item.mensagem,
            func_id=item.funcionario_id,
            tipo=item.tipo or 'fila',
        )
        if ok:
            item.status = 'enviado'
            item.enviado_em = datetime.utcnow()
            enviados += 1
        else:
            if item.tentativas >= 3:
                item.status = 'erro'
            erros += 1
    db.session.commit()
    return {'enviados': enviados, 'erros': erros}


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
                regra.ultima_execucao = datetime.utcnow()
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

            if not matched:
                continue

            # Cooldown: evita reenviar o mesmo tipo de alerta no mesmo período
            if _em_cooldown(func.id, regra.condition_type, data_ref):
                continue

            enviados_regra += _enviar(regra, func, minutos, aloc, data_ref)

        if enviados_regra > 0:
            regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + enviados_regra
            regra.ultima_execucao = datetime.utcnow()
        total += enviados_regra

    db.session.commit()
    return {'regras': len(regras), 'mensagens': total}


def processar_regras_agendadas() -> dict:
    """
    Verifica regras DAILY e WEEKLY para a hora/dia atual.
    Chamado a cada hora via Celery beat.
    """
    agora = datetime.now()
    hora_atual = agora.hour
    dia_atual  = agora.weekday()

    regras = NotificationRule.query.filter(
        NotificationRule.ativo == True,
        NotificationRule.trigger_type.in_(['DAILY', 'WEEKLY']),
        NotificationRule.trigger_hour == hora_atual,
    ).all()

    total = 0
    for regra in regras:
        if regra.trigger_type == 'WEEKLY' and regra.trigger_weekday != dia_atual:
            continue
        result = processar_regras_evento(regra.trigger_type)
        total += result.get('mensagens', 0)

    return {'total': total}
