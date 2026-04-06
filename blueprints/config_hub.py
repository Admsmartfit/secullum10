"""
Módulo de Configuração do Sistema.
Gerencia: usuários, líderes de unidade, teste WhatsApp, importação de escalas Secullum.
"""
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from extensions import db
from models import Usuario, Funcionario, UnidadeLider, AlocacaoDiaria, Turno, Configuracao, Feriado

config_hub_bp = Blueprint('config_hub', __name__, url_prefix='/config')


def _somente_gestor(f):
    """Decorator simples para restringir a administradores."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.nivel_acesso != 'administrador':
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated


# ── Página principal (tabs) ───────────────────────────────────────────────────

@config_hub_bp.route('/')
@login_required
@_somente_gestor
def index():
    usuarios = Usuario.query.order_by(Usuario.nome).all()
    # Departamentos únicos presentes no banco
    depts = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    departamentos = [d[0] for d in depts if d[0]]
    unidades = {u.departamento: u for u in UnidadeLider.query.all()}
    # Funcionários sem escala nos próximos 7 dias
    hoje = date.today()
    limite = hoje + timedelta(days=7)
    com_escala = (
        db.session.query(AlocacaoDiaria.funcionario_id)
        .filter(AlocacaoDiaria.data.between(hoje, limite))
        .distinct()
        .subquery()
    )
    sem_escala = (
        Funcionario.query
        .filter(Funcionario.ativo == True, Funcionario.id.notin_(db.session.query(com_escala)))
        .order_by(Funcionario.nome)
        .all()
    )
    todos_func = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    import os

    def _cfg(chave, default=''):
        row = Configuracao.query.filter_by(chave=chave).first()
        return row.valor if row and row.valor is not None else default

    sync_cfg = {
        'rapida_ativo':           _cfg('sync_rapida_ativo', '1') == '1',
        'rapida_intervalo_min':   _cfg('sync_rapida_intervalo_min', '10'),
        'completa_ativo':         _cfg('sync_completa_ativo', '1') == '1',
        'completa_intervalo_min': _cfg('sync_completa_intervalo_min', '60'),
        'completa_janela_horas':  _cfg('sync_completa_janela_horas', '12'),
        'rapida_ultimo_run':      _cfg('sync_rapida_ultimo_run', ''),
        'completa_ultimo_run':    _cfg('sync_completa_ultimo_run', ''),
        # Verificação de inconsistências (DB vs Secullum)
        'verificar_incons_ativo':           _cfg('verificar_incons_ativo', '0') == '1',
        'verificar_incons_hora':            _cfg('verificar_incons_hora', '01:00'),
        'verificar_incons_ultimo_run':      _cfg('verificar_incons_ultimo_run', ''),
        'verificar_incons_ultimo_resultado': _cfg('verificar_incons_ultimo_resultado', ''),
    }

    from models import TabelaSalarial
    funcoes_db = (
        db.session.query(Funcionario.funcao)
        .filter(Funcionario.ativo == True, Funcionario.funcao.isnot(None))
        .distinct()
        .order_by(Funcionario.funcao)
        .all()
    )
    funcoes_sal = [f[0] for f in funcoes_db if f[0]]
    salarios_map = {s.funcao: s for s in TabelaSalarial.query.all()}
    cfg_exp = Configuracao.query.filter_by(chave='experiencia_dias').first()
    experiencia_dias = int(cfg_exp.valor) if cfg_exp and cfg_exp.valor else 45

    from services.config_service import get_setting
    integ_cfg = {
        'secullum_email':    get_setting('secullum_email',    'SECULLUM_EMAIL',    ''),
        'secullum_password': get_setting('secullum_password', 'SECULLUM_PASSWORD', ''),
        'secullum_banco':    get_setting('secullum_banco',    'SECULLUM_BANCO',    ''),
        'megaapi_host':      get_setting('megaapi_host',      'MEGAAPI_HOST',      'apistart01.megaapi.com.br'),
        'megaapi_instance':  get_setting('megaapi_instance',  'MEGAAPI_INSTANCE',  ''),
        'megaapi_token':        get_setting('megaapi_token',        'MEGAAPI_TOKEN',  ''),
        'megaapi_secret':       get_setting('megaapi_secret',       'MEGAAPI_SECRET', ''),
        'gestor_celular':       get_setting('gestor_celular',       'GESTOR_CELULAR', ''),
        'calendario_api_token': get_setting('calendario_api_token', '',               ''),
    }

    rh_politicas = {
        'tolerancia_ponto':   _cfg('tolerancia_ponto_minutos', '10'),
        'fecho_folha_inicio': _cfg('fecho_folha_inicio', '1'),
        'fecho_folha_fim':    _cfg('fecho_folha_fim', '31'),
        'descontar_dsr':      _cfg('descontar_dsr', '0') == '1',
    }
    ano_feriados = int(request.args.get('ano_feriados', date.today().year))
    feriados = Feriado.query.filter(
        db.extract('year', Feriado.data) == ano_feriados
    ).order_by(Feriado.data).all()

    return render_template(
        'config/index.html',
        usuarios=usuarios,
        departamentos=departamentos,
        unidades=unidades,
        sem_escala=sem_escala,
        todos_func=todos_func,
        hoje=hoje.strftime('%Y-%m-%d'),
        fim30=(hoje + timedelta(days=30)).strftime('%Y-%m-%d'),
        megaapi_token=bool(integ_cfg['megaapi_token']),
        megaapi_instance=bool(integ_cfg['megaapi_instance']),
        sync_cfg=sync_cfg,
        rh_politicas=rh_politicas,
        feriados=feriados,
        ano_feriados=ano_feriados,
        ano_atual=date.today().year,
        funcoes_sal=funcoes_sal,
        salarios_map=salarios_map,
        experiencia_dias=experiencia_dias,
        integ_cfg=integ_cfg,
        mapa_cidades={u.cidade_ibge: u.empresa_cidade for u in unidades.values() if u.cidade_ibge},
    )


# ── Usuários ──────────────────────────────────────────────────────────────────

@config_hub_bp.route('/usuarios/novo', methods=['POST'])
@login_required
@_somente_gestor
def usuario_novo():
    nome = request.form.get('nome', '').strip()
    email = request.form.get('email', '').strip().lower()
    senha = request.form.get('senha', '').strip()
    nivel = request.form.get('nivel_acesso', 'funcionario')

    if not nome or not email or not senha:
        flash('Preencha nome, e-mail e senha.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    if Usuario.query.filter_by(email=email).first():
        flash(f'E-mail {email} já cadastrado.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    u = Usuario(nome=nome, email=email, nivel_acesso=nivel, ativo=True)
    u.set_senha(senha)
    db.session.add(u)
    db.session.commit()
    flash(f'Usuário {nome} criado com sucesso.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


@config_hub_bp.route('/usuarios/<int:uid>/editar', methods=['POST'])
@login_required
@_somente_gestor
def usuario_editar(uid):
    u = Usuario.query.get_or_404(uid)
    u.nome = request.form.get('nome', u.nome).strip()
    email = request.form.get('email', u.email).strip().lower()
    nivel = request.form.get('nivel_acesso', u.nivel_acesso)
    ativo = request.form.get('ativo') == '1'
    nova_senha = request.form.get('senha', '').strip()

    # Verificar duplicata de e-mail
    outro = Usuario.query.filter(Usuario.email == email, Usuario.id != uid).first()
    if outro:
        flash('E-mail já utilizado por outro usuário.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    u.email = email
    u.nivel_acesso = nivel
    u.ativo = ativo
    if nova_senha:
        u.set_senha(nova_senha)
    db.session.commit()
    flash(f'Usuário {u.nome} atualizado.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


@config_hub_bp.route('/usuarios/<int:uid>/excluir', methods=['POST'])
@login_required
@_somente_gestor
def usuario_excluir(uid):
    u = Usuario.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('Você não pode excluir seu próprio usuário.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')
    db.session.delete(u)
    db.session.commit()
    flash(f'Usuário {u.nome} excluído.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


# ── Unidades / Líderes ────────────────────────────────────────────────────────

@config_hub_bp.route('/unidades/salvar', methods=['POST'])
@login_required
@_somente_gestor
def unidades_salvar():
    """Salva mapeamento departamento → líder + celular."""
    dados = request.get_json(force=True) or {}
    salvos = 0
    for item in dados.get('unidades', []):
        dept = (item.get('departamento') or '').strip()
        if not dept:
            continue
        unidade = UnidadeLider.query.filter_by(departamento=dept).first()
        if not unidade:
            unidade = UnidadeLider(departamento=dept)
            db.session.add(unidade)
        unidade.nome_unidade     = (item.get('nome_unidade') or dept).strip()
        unidade.celular_lider    = (item.get('celular_lider') or '').strip()
        lider_id = item.get('lider_id')
        unidade.lider_id         = int(lider_id) if lider_id else None
        unidade.empresa_nome     = (item.get('empresa_nome') or '').strip() or None
        unidade.empresa_cnpj     = (item.get('empresa_cnpj') or '').strip() or None
        unidade.empresa_socio    = (item.get('empresa_socio') or '').strip() or None
        unidade.socio_cpf        = (item.get('socio_cpf') or '').strip() or None
        unidade.empresa_endereco = (item.get('empresa_endereco') or '').strip() or None
        unidade.empresa_cidade   = (item.get('empresa_cidade') or '').strip() or None
        unidade.empresa_uf       = (item.get('empresa_uf') or '').strip() or None
        unidade.empresa_cep      = (item.get('empresa_cep') or '').strip() or None
        unidade.cidade_ibge      = (item.get('cidade_ibge') or '').strip() or None
        exp = item.get('experiencia_dias')
        unidade.experiencia_dias = int(exp) if exp else 45
        salvos += 1
    db.session.commit()
    return jsonify({'ok': True, 'salvos': salvos})


# ── Teste de WhatsApp ─────────────────────────────────────────────────────────

@config_hub_bp.route('/whatsapp/testar', methods=['POST'])
@login_required
@_somente_gestor
def whatsapp_testar():
    celular = request.form.get('celular', '').strip()
    mensagem = request.form.get('mensagem', 'Teste de comunicação — Secullum Hub ✅').strip()
    if not celular:
        flash('Informe o número de celular para teste.', 'danger')
        return redirect(url_for('config_hub.index') + '#whatsapp')
    from services.whatsapp_bot import enviar_texto
    ok = enviar_texto(celular=celular, mensagem=mensagem, tipo='teste')
    if ok:
        flash(f'Mensagem enviada com sucesso para {celular}!', 'success')
    else:
        flash(
            f'Falha no envio para {celular}. Verifique MEGAAPI_TOKEN e MEGAAPI_INSTANCE no .env. '
            'O log foi registrado em WhatsApp → Logs.',
            'warning',
        )
    return redirect(url_for('config_hub.index') + '#whatsapp')


# ── Importar Escalas do Secullum ──────────────────────────────────────────────

# Secullum DiaSemana: 0=Dom, 1=Seg, 2=Ter, 3=Qua, 4=Qui, 5=Sex, 6=Sab
# Python weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
_SECULLUM_TO_PYTHON = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
_DIAS_NOMES = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def _parsear_horario(horario_raw: dict) -> dict:
    """Converte um item de /Horarios em dict simplificado para o frontend."""
    nome = horario_raw.get('Descricao') or f"Horário {horario_raw.get('Id')}"
    dias_trabalho = []
    hora_inicio = ''
    hora_fim = ''

    for dia in horario_raw.get('Dias', []):
        entrada1 = (dia.get('Entrada1') or '').strip()
        if not entrada1 or entrada1 == '00:00':
            continue  # dia de folga
        dia_sec = dia.get('DiaSemana')
        py_wd = _SECULLUM_TO_PYTHON.get(dia_sec)
        if py_wd is not None:
            dias_trabalho.append(py_wd)

        # Pega início/fim do primeiro dia útil encontrado
        if not hora_inicio:
            hora_inicio = entrada1[:5]
            # Última saída não-vazia do dia
            for i in range(5, 0, -1):
                saida = (dia.get(f'Saida{i}') or '').strip()
                if saida and saida != '00:00':
                    hora_fim = saida[:5]
                    break

    return {
        'horario_id': horario_raw.get('Id'),
        'nome_horario': nome,
        'hora_inicio': hora_inicio,
        'hora_fim': hora_fim,
        'dias_semana': sorted(set(dias_trabalho)),
        'dias_label': ', '.join(_DIAS_NOMES[d] for d in sorted(set(dias_trabalho))),
    }


@config_hub_bp.route('/escalas/preview', methods=['POST'])
@login_required
@_somente_gestor
def escalas_preview():
    """Busca HorarioId de cada funcionário e retorna detalhes do horário para preview."""
    import os
    func_ids_req = [str(f) for f in (request.get_json(force=True) or {}).get('func_ids', [])]
    if not func_ids_req:
        return jsonify({'error': 'Nenhum funcionário selecionado.'}), 400

    from services.config_service import get_secullum_api
    api = get_secullum_api()

    # 1. Busca todos os horários (uma chamada)
    horarios_raw = api.listar_horarios()
    if not horarios_raw:
        return jsonify({'error': 'Não foi possível obter os horários da API Secullum. Verifique as credenciais.'}), 502
    horarios_map = {str(h['Id']): _parsear_horario(h) for h in horarios_raw}

    # 2. Busca funcionários da API para obter HorarioId
    funcs_api = api.listar_funcionarios()
    if not funcs_api:
        return jsonify({'error': 'Não foi possível obter funcionários da API Secullum.'}), 502
    funcs_api_map = {str(f['Id']): f for f in funcs_api}

    # 3. Cruza com funcionários selecionados
    resultados = []
    sem_horario = []
    for fid in func_ids_req[:100]:
        func_db = Funcionario.query.get(fid)
        func_api = funcs_api_map.get(fid)
        nome = func_db.nome if func_db else fid

        if not func_api:
            sem_horario.append(nome)
            continue

        horario_id = str(func_api.get('HorarioId') or '')
        if not horario_id or horario_id not in horarios_map:
            sem_horario.append(nome)
            continue

        h = horarios_map[horario_id]
        if not h['hora_inicio']:
            sem_horario.append(nome)
            continue

        resultados.append({
            'func_id': fid,
            'nome': nome,
            'horario_id': h['horario_id'],
            'nome_horario': h['nome_horario'],
            'hora_inicio': h['hora_inicio'],
            'hora_fim': h['hora_fim'],
            'dias_semana': h['dias_semana'],
            'dias_label': h['dias_label'],
        })

    if not resultados:
        msg = 'Nenhum dos funcionários selecionados possui horário configurado no Secullum.'
        if sem_horario:
            msg += f' Sem horário: {", ".join(sem_horario[:5])}{"..." if len(sem_horario) > 5 else ""}.'
        return jsonify({'error': msg}), 404

    return jsonify({'horarios': resultados, 'sem_horario': sem_horario})


@config_hub_bp.route('/escalas/importar', methods=['POST'])
@login_required
@_somente_gestor
def escalas_importar():
    """Cria Turnos e AlocacaoDiarias a partir do preview confirmado."""
    from datetime import datetime as dt
    dados = request.get_json(force=True) or {}
    horarios = dados.get('horarios', [])
    data_inicio_str = dados.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = dados.get('data_fim', (date.today() + timedelta(days=30)).strftime('%Y-%m-%d'))

    try:
        d_inicio = dt.strptime(data_inicio_str, '%Y-%m-%d').date()
        d_fim = dt.strptime(data_fim_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Datas inválidas.'}), 400

    turnos_criados = alocacoes_criadas = erros = 0

    for h in horarios:
        nome_turno = (h.get('nome_horario') or 'Sem nome').strip()
        hora_inicio_str = (h.get('hora_inicio') or '').strip()[:5]
        hora_fim_str = (h.get('hora_fim') or '').strip()[:5]
        dias_python = [int(d) for d in (h.get('dias_semana') or [])]
        func_id = str(h.get('func_id', ''))

        if not hora_inicio_str or not hora_fim_str or not func_id or not dias_python:
            erros += 1
            continue

        try:
            t_inicio = dt.strptime(hora_inicio_str, '%H:%M').time()
            t_fim = dt.strptime(hora_fim_str, '%H:%M').time()
        except ValueError:
            erros += 1
            continue

        dias_str = ','.join(str(d) for d in sorted(set(dias_python)))

        turno = Turno.query.filter_by(nome=nome_turno, hora_inicio=t_inicio, hora_fim=t_fim).first()
        if not turno:
            turno = Turno(nome=nome_turno, hora_inicio=t_inicio, hora_fim=t_fim, dias_semana=dias_str)
            db.session.add(turno)
            db.session.flush()
            turnos_criados += 1

        cur = d_inicio
        while cur <= d_fim:
            if cur.weekday() in dias_python:
                existe = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=cur).first()
                if not existe:
                    db.session.add(AlocacaoDiaria(funcionario_id=func_id, turno_id=turno.id, data=cur))
                    alocacoes_criadas += 1
            cur += timedelta(days=1)

    db.session.commit()
    return jsonify({
        'ok': True,
        'turnos_criados': turnos_criados,
        'alocacoes_criadas': alocacoes_criadas,
        'erros': erros,
    })


# ── Sync Automático de Batidas ────────────────────────────────────────────────

def _salvar_cfg(chave, valor):
    row = Configuracao.query.filter_by(chave=chave).first()
    if row:
        row.valor = str(valor)
    else:
        db.session.add(Configuracao(chave=chave, valor=str(valor)))


@config_hub_bp.route('/sync-batidas/salvar', methods=['POST'])
@login_required
@_somente_gestor
def sync_batidas_salvar():
    """Salva parâmetros do sync automático de batidas."""
    rapida_ativo           = '1' if request.form.get('rapida_ativo') else '0'
    rapida_intervalo_min   = request.form.get('rapida_intervalo_min', '10').strip()
    completa_ativo         = '1' if request.form.get('completa_ativo') else '0'
    completa_intervalo_min = request.form.get('completa_intervalo_min', '60').strip()
    completa_janela_horas  = request.form.get('completa_janela_horas', '12').strip()

    try:
        assert 1 <= int(rapida_intervalo_min) <= 1440
        assert 1 <= int(completa_intervalo_min) <= 1440
        assert 1 <= int(completa_janela_horas) <= 168
    except (ValueError, AssertionError):
        flash('Valores inválidos. Verifique os intervalos informados.', 'danger')
        return redirect(url_for('config_hub.index') + '#tab-sync')

    _salvar_cfg('sync_rapida_ativo',           rapida_ativo)
    _salvar_cfg('sync_rapida_intervalo_min',   rapida_intervalo_min)
    _salvar_cfg('sync_completa_ativo',         completa_ativo)
    _salvar_cfg('sync_completa_intervalo_min', completa_intervalo_min)
    _salvar_cfg('sync_completa_janela_horas',  completa_janela_horas)
    db.session.commit()

    flash('Configurações de sync automático salvas com sucesso.', 'success')
    return redirect(url_for('config_hub.index') + '#tab-sync')


@config_hub_bp.route('/sync-batidas/executar', methods=['POST'])
@login_required
@_somente_gestor
def sync_batidas_executar():
    """Executa manualmente o sync de batidas (incremental ou completo)."""
    tipo = request.form.get('tipo', 'rapida')
    from zoneinfo import ZoneInfo
    _tz = ZoneInfo('America/Sao_Paulo')
    
    try:
        if tipo == 'completa':
            from tasks import _get_cfg
            from datetime import datetime, timedelta
            from services.sync_service import sync_batidas
            janela = int(_get_cfg('sync_completa_janela_horas', '12'))
            agora = datetime.now(_tz)
            ok, msg = sync_batidas(
                (agora - timedelta(hours=janela)).strftime('%Y-%m-%d'),
                agora.strftime('%Y-%m-%d'),
                (agora - timedelta(hours=janela)).strftime('%H:%M'),
                agora.strftime('%H:%M'),
            )
            _salvar_cfg('sync_completa_ultimo_run', agora.isoformat())
            db.session.commit()
        else:
            from services.sync_service import sync_batidas_incremental
            from datetime import datetime
            ok, msg = sync_batidas_incremental()
            _salvar_cfg('sync_rapida_ultimo_run', datetime.now(_tz).isoformat())
            db.session.commit()
            
        return jsonify({'ok': ok, 'message': msg})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


# ── Verificação de Inconsistências (DB vs Secullum) ───────────────────────────

@config_hub_bp.route('/verificar-inconsistencias/salvar', methods=['POST'])
@login_required
@_somente_gestor
def verificar_incons_salvar():
    """Salva configuração da verificação automática de inconsistências."""
    ativo = '1' if request.form.get('verificar_incons_ativo') else '0'
    hora  = request.form.get('verificar_incons_hora', '01:00').strip()

    # Valida formato HH:MM
    try:
        h, m = hora.split(':')
        assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AssertionError):
        flash('Horário inválido. Use o formato HH:MM.', 'danger')
        return redirect(url_for('config_hub.index') + '#tab-sync')

    _salvar_cfg('verificar_incons_ativo', ativo)
    _salvar_cfg('verificar_incons_hora',  hora)
    db.session.commit()

    flash('Configuração de verificação de inconsistências salva.', 'success')
    return redirect(url_for('config_hub.index') + '#tab-sync')


@config_hub_bp.route('/verificar-inconsistencias/executar', methods=['POST'])
@login_required
@_somente_gestor
def verificar_incons_executar():
    """Executa manualmente a verificação DB vs Secullum para o dia anterior."""
    from datetime import timedelta, datetime
    from zoneinfo import ZoneInfo
    from services.report_service import disparar_relatorio_inconsistencias
    
    _tz_br = ZoneInfo('America/Sao_Paulo')
    agora = datetime.now(_tz_br)
    ontem = (agora.date() - timedelta(days=1))
    ontem_str = ontem.strftime('%Y-%m-%d')

    try:
        from services.sync_service import sync_batidas
        ok, msg = sync_batidas(ontem_str, ontem_str)
        resultado = f'Sincronização de {ontem_str}: {msg}'
        _salvar_cfg('verificar_incons_ultimo_resultado', resultado)
        _salvar_cfg('verificar_incons_ultimo_run', agora.isoformat())
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f'[verificar_incons] Erro na sincronização: {e}')
        return jsonify({'ok': False, 'resultado': f'Erro na sincronização: {e}', 'error': str(e)}), 500

    total_env = 0
    try:
        total_env = disparar_relatorio_inconsistencias(ontem)
    except Exception as e:
        current_app.logger.error(f'[verificar_incons] Erro ao disparar relatórios: {e}')
        resultado += f' | Erro no envio: {e}'

    if total_env == 0:
        resultado += f' | Relatório gerado mas nenhum destinatário configurado (configure o celular do gestor ou líderes de departamento).'
    else:
        resultado += f' | Relatórios enviados: {total_env}.'

    return jsonify({'ok': True, 'resultado': resultado})


# ── Políticas de RH ───────────────────────────────────────────────────────────

@config_hub_bp.route('/politicas/salvar', methods=['POST'])
@login_required
@_somente_gestor
def politicas_salvar():
    """Salva parâmetros de políticas de RH (Tolerância, Fecho, DSR)."""
    tolerancia = request.form.get('tolerancia_ponto', '10').strip()
    fecho_inicio = request.form.get('fecho_folha_inicio', '1').strip()
    fecho_fim = request.form.get('fecho_folha_fim', '31').strip()
    descontar_dsr = '1' if request.form.get('descontar_dsr') else '0'
    experiencia = request.form.get('experiencia_dias', '45').strip()

    _salvar_cfg('tolerancia_ponto_minutos', tolerancia)
    _salvar_cfg('fecho_folha_inicio', fecho_inicio)
    _salvar_cfg('fecho_folha_fim', fecho_fim)
    _salvar_cfg('descontar_dsr', descontar_dsr)
    _salvar_cfg('experiencia_dias', experiencia)
    
    db.session.commit()
    flash('Políticas de RH atualizadas com sucesso.', 'success')
    return redirect(url_for('config_hub.index') + '#politicas')


# ── Feriados ──────────────────────────────────────────────────────────────────

@config_hub_bp.route('/feriados/novo', methods=['POST'])
@login_required
@_somente_gestor
def feriado_novo():
    data_str = request.form.get('data')
    descricao = request.form.get('descricao', '').strip()
    uf = request.form.get('uf', '').strip().upper() or None
    cidade_ibge = request.form.get('cidade_ibge', '').strip() or None
    tipo = request.form.get('tipo', 'personalizado')

    if not data_str or not descricao:
        flash('Data e descrição são obrigatórias.', 'danger')
        return redirect(url_for('config_hub.index') + '#feriados')

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
        if Feriado.query.filter_by(data=data, tipo=tipo, uf=uf, cidade_ibge=cidade_ibge).first():
            flash('Feriado já cadastrado para esta data/tipo/localidade.', 'warning')
        else:
            f = Feriado(
                data=data, descricao=descricao, tipo=tipo,
                uf=uf, cidade_ibge=cidade_ibge, fonte='manual',
                ativo=True, criado_por_id=current_user.id,
            )
            db.session.add(f)
            db.session.commit()
            flash('Feriado adicionado com sucesso.', 'success')
    except Exception as e:
        flash(f'Erro ao adicionar feriado: {e}', 'danger')

    return redirect(url_for('config_hub.index') + '#feriados')


@config_hub_bp.route('/feriados/<int:fid>/toggle', methods=['POST'])
@login_required
@_somente_gestor
def feriado_toggle(fid):
    f = Feriado.query.get_or_404(fid)
    f.ativo = not f.ativo
    db.session.commit()
    return jsonify({'ok': True, 'ativo': f.ativo})


@config_hub_bp.route('/feriados/<int:fid>/excluir', methods=['POST'])
@login_required
@_somente_gestor
def feriado_excluir(fid):
    f = Feriado.query.get_or_404(fid)
    db.session.delete(f)
    db.session.commit()
    flash('Feriado excluído.', 'success')
    return redirect(url_for('config_hub.index') + '#feriados')


@config_hub_bp.route('/feriados/sync', methods=['POST'])
@login_required
@_somente_gestor
def feriados_sync():
    """Sincroniza feriados via APIs externas para o ano solicitado."""
    from services.feriados_service import sincronizar_feriados
    try:
        ano = int(request.form.get('ano', date.today().year))
    except (ValueError, TypeError):
        ano = date.today().year
    result = sincronizar_feriados(ano, usuario_id=current_user.id)
    criados = result.get('criados', 0)
    avisos = result.get('avisos', [])
    msg = f'{criados} feriado(s) importado(s) para {ano}.'
    if avisos:
        msg += ' Avisos: ' + '; '.join(avisos[:2])
    flash(msg, 'success' if not avisos else 'warning')
    return redirect(url_for('config_hub.index') + '#feriados')



# ── Integrações (credenciais via DB) ──────────────────────────────────────────

@config_hub_bp.route('/integracoes/salvar', methods=['POST'])
@login_required
@_somente_gestor
def integracoes_salvar():
    """Salva credenciais de integração no banco (sem precisar editar .env)."""
    campos = [
        ('secullum_email',    'SECULLUM_EMAIL'),
        ('secullum_password', 'SECULLUM_PASSWORD'),
        ('secullum_banco',    'SECULLUM_BANCO'),
        ('megaapi_host',      'MEGAAPI_HOST'),
        ('megaapi_instance',  'MEGAAPI_INSTANCE'),
        ('megaapi_token',        'MEGAAPI_TOKEN'),
        ('megaapi_secret',       'MEGAAPI_SECRET'),
        ('gestor_celular',       'GESTOR_CELULAR'),
        ('calendario_api_token', ''),
    ]
    for chave_db, form_field in campos:
        valor = request.form.get(form_field, '').strip()
        if valor:
            _salvar_cfg(chave_db, valor)
    db.session.commit()
    flash('Credenciais de integração salvas com sucesso.', 'success')
    return redirect(url_for('config_hub.index') + '#tab-integracoes')
