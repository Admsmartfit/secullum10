from datetime import date, datetime, timedelta
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Turno, AlocacaoDiaria, Funcionario, Batida, PadraoTurno, GrupoDepartamento
from services.motor_clt import validar_alocacao

escalas_bp = Blueprint('escalas', __name__, url_prefix='/escalas')

DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


# ── Turnos CRUD ───────────────────────────────────────────────────────────────

@escalas_bp.route('/')
@login_required
def index():
    turnos = Turno.query.order_by(Turno.nome).all()
    return render_template('escalas/index.html', turnos=turnos, dias=DIAS_SEMANA)


@escalas_bp.route('/turno/novo', methods=['GET', 'POST'])
@login_required
def turno_novo():
    if request.method == 'POST':
        dias = request.form.getlist('dias_semana')
        intervalo = int(request.form.get('intervalo_minutos', 60))
        
        # Horários complexos por dia
        complex_data = {}
        for d_idx in dias:
            prefix = f"dia_{d_idx}"
            h_ini = request.form.get(f"{prefix}_inicio")
            h_fim = request.form.get(f"{prefix}_fim")
            h_int = request.form.get(f"{prefix}_intervalo")
            if h_ini and h_fim:
                complex_data[d_idx] = {
                    "inicio": h_ini,
                    "fim": h_fim,
                    "intervalo": int(h_int) if h_int else intervalo
                }

        turno = Turno(
            nome=request.form['nome'].strip(),
            hora_inicio=datetime.strptime(request.form['hora_inicio'], '%H:%M').time(),
            hora_fim=datetime.strptime(request.form['hora_fim'], '%H:%M').time(),
            intervalo_minutos=intervalo,
            dias_semana=','.join(dias) if dias else '0,1,2,3,4',
            dias_complexos_json=json.dumps(complex_data) if complex_data else None,
            departamento=request.form.get('departamento', '').strip() or None,
            color=request.form.get('color', '#4f46e5') or '#4f46e5',
        )
        db.session.add(turno)
        db.session.commit()
        flash(f'Turno "{turno.nome}" criado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=None, dias=DIAS_SEMANA,
                           departamentos=_departamentos())


@escalas_bp.route('/turno/<int:turno_id>/editar', methods=['GET', 'POST'])
@login_required
def turno_editar(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    if request.method == 'POST':
        dias = request.form.getlist('dias_semana')
        intervalo = int(request.form.get('intervalo_minutos', 60))
        
        complex_data = {}
        for d_idx in dias:
            prefix = f"dia_{d_idx}"
            h_ini = request.form.get(f"{prefix}_inicio")
            h_fim = request.form.get(f"{prefix}_fim")
            h_int = request.form.get(f"{prefix}_intervalo")
            if h_ini and h_fim:
                complex_data[d_idx] = {
                    "inicio": h_ini,
                    "fim": h_fim,
                    "intervalo": int(h_int) if h_int else intervalo
                }

        turno.nome = request.form['nome'].strip()
        turno.hora_inicio = datetime.strptime(request.form['hora_inicio'], '%H:%M').time()
        turno.hora_fim = datetime.strptime(request.form['hora_fim'], '%H:%M').time()
        turno.intervalo_minutos = intervalo
        turno.dias_semana = ','.join(dias) if dias else '0,1,2,3,4'
        turno.dias_complexos_json = json.dumps(complex_data) if complex_data else None
        turno.departamento = request.form.get('departamento', '').strip() or None
        turno.color = request.form.get('color', '#4f46e5') or '#4f46e5'
        db.session.commit()
        flash(f'Turno "{turno.nome}" atualizado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=turno, dias=DIAS_SEMANA,
                           departamentos=_departamentos())


@escalas_bp.route('/turno/<int:turno_id>/excluir', methods=['POST'])
@login_required
def turno_excluir(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    db.session.delete(turno)
    db.session.commit()
    flash(f'Turno "{turno.nome}" excluído.', 'warning')
    return redirect(url_for('escalas.index'))


# ── Alocações ─────────────────────────────────────────────────────────────────

def _aplicar_escala_futuro(func_id, turno_id: int, dias_semana: list, data_inicio, dias_frente: int = 60) -> int:
    """Substitui todas as alocações futuras do funcionário pelo novo turno,
    apenas nos dias da semana configurados no turno."""
    data_fim = data_inicio + timedelta(days=dias_frente)
    AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id == func_id,
        AlocacaoDiaria.data >= data_inicio,
        AlocacaoDiaria.data <= data_fim,
    ).delete()
    gerados = 0
    d = data_inicio
    while d <= data_fim:
        if d.weekday() in dias_semana:
            db.session.add(AlocacaoDiaria(funcionario_id=func_id, turno_id=turno_id, data=d))
            gerados += 1
        d += timedelta(days=1)
    return gerados


@escalas_bp.route('/alocar', methods=['GET', 'POST'])
@login_required
def alocar():
    funcionarios = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    turnos = Turno.query.order_by(Turno.nome).all()

    if request.method == 'POST':
        func_id = request.form['funcionario_id']
        turno_id = int(request.form['turno_id'])
        data_aloc = datetime.strptime(request.form['data'], '%Y-%m-%d').date()

        turno = Turno.query.get_or_404(turno_id)

        # Validação CLT — bloqueante vs. warning
        infracoes = validar_alocacao(func_id, data_aloc, turno)
        bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
        avisos      = [i for i in infracoes if i.get('severity', 'error') != 'error']
        if bloqueantes:
            return jsonify({'ok': False, 'infracoes': bloqueantes}), 422
        compliance_warn = '; '.join(i['message'] for i in avisos) or None

        aplicar_futuro = request.form.get('aplicar_futuro') == '1'

        if aplicar_futuro:
            # Substitui todas as alocações futuras pelo novo turno,
            # respeitando apenas os dias da semana configurados no turno.
            dias_turno = turno.dias_semana_list  # ex: [0,1,2,3,4]
            dias_gerados = _aplicar_escala_futuro(func_id, turno_id, dias_turno, data_aloc)
            db.session.commit()
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'ok': True, 'message': f'Escala atualizada! {dias_gerados} dias aplicados.'})
            flash(f'Escala do funcionário atualizada para os próximos 60 dias ({dias_gerados} dias)!', 'success')
        else:
            # Upsert de um único dia
            aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=data_aloc).first()
            if aloc:
                aloc.turno_id = turno_id
                aloc.compliance_warning = compliance_warn
            else:
                aloc = AlocacaoDiaria(funcionario_id=func_id, turno_id=turno_id,
                                      data=data_aloc, compliance_warning=compliance_warn)
                db.session.add(aloc)
            db.session.commit()
            msg = 'Alocação salva!'
            if avisos:
                msg += ' Atenção: ' + '; '.join(i['message'] for i in avisos)
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'ok': True, 'message': msg, 'warnings': avisos})
            flash(msg, 'success' if not avisos else 'warning')

        return redirect(url_for('escalas.alocar'))

    # Pré-selecionar data
    data_sel = request.args.get('data', date.today().strftime('%Y-%m-%d'))
    return render_template('escalas/alocar.html',
                           funcionarios=funcionarios, turnos=turnos, data_sel=data_sel,
                           departamentos=_departamentos())


# ── Divergências ──────────────────────────────────────────────────────────────

@escalas_bp.route('/divergencias')
@login_required
def divergencias():
    data_str = request.args.get('data', date.today().strftime('%Y-%m-%d'))
    data_ref = datetime.strptime(data_str, '%Y-%m-%d').date()

    # Funcionários com alocação hoje
    alocacoes = AlocacaoDiaria.query.filter_by(data=data_ref).all()

    # Quais têm batida?
    func_com_batida = {
        b.funcionario_id
        for b in Batida.query.filter_by(data=data_ref).all()
    }

    ausentes = [
        {
            'funcionario': a.funcionario.nome,
            'funcionario_id': a.funcionario_id,
            'turno': a.turno.nome,
            'hora_inicio': a.turno.hora_inicio.strftime('%H:%M'),
            'celular': a.funcionario.celular,
        }
        for a in alocacoes
        if a.funcionario_id not in func_com_batida
    ]

    if request.args.get('fmt') == 'json':
        return jsonify(ausentes)

    return render_template('escalas/divergencias.html',
                           ausentes=ausentes, data=data_str, total=len(ausentes))


# ── Calendário FullCalendar ────────────────────────────────────────────────────

@escalas_bp.route('/calendario')
@login_required
def calendario():
    departamentos = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    turnos = Turno.query.order_by(Turno.nome).all()
    funcionarios = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    return render_template(
        'escalas/calendario.html',
        departamentos=[d[0] for d in departamentos],
        turnos=turnos,
        funcionarios=funcionarios,
    )


@escalas_bp.route('/eventos')
@login_required
def eventos():
    """JSON endpoint para FullCalendar — retorna AlocacaoDiarias como eventos."""
    start_str = request.args.get('start', '')
    end_str   = request.args.get('end', '')
    dept      = request.args.get('dept', '').strip()
    func_id   = request.args.get('func_id', '').strip()
    funcao    = request.args.get('funcao', '').strip()

    try:
        d_ini = date.fromisoformat(start_str[:10])
        d_fim = date.fromisoformat(end_str[:10])
    except (ValueError, TypeError):
        return jsonify([])

    q = (
        AlocacaoDiaria.query
        .filter(AlocacaoDiaria.data >= d_ini, AlocacaoDiaria.data <= d_fim)
        .join(Turno)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
    )
    q = _filtrar_dept(q, dept)
    if funcao:
        q = q.filter(Funcionario.funcao == funcao)
    if func_id:
        q = q.filter(AlocacaoDiaria.funcionario_id == func_id)

    alocacoes = q.order_by(AlocacaoDiaria.data, Funcionario.nome).limit(500).all()

    events = []
    for aloc in alocacoes:
        infracoes = validar_alocacao(aloc.funcionario_id, aloc.data, aloc.turno)
        
        # Pega o horário específico para aquele dia da semana deste turno
        h_ini_t, h_fim_t, _ = aloc.turno.get_horario_dia(aloc.data.weekday())
        hora_ini = h_ini_t.strftime('%H:%M')
        hora_fim = h_fim_t.strftime('%H:%M')
        
        nome_parts = aloc.funcionario.nome.split()
        titulo = nome_parts[0] + (f' {nome_parts[1][0]}.' if len(nome_parts) > 1 else '')

        base_color = aloc.turno.color or '#4f46e5'
        events.append({
            'id': aloc.id,
            'resourceId': str(aloc.funcionario_id),   # para resource view
            'title': titulo,
            'start': f"{aloc.data}T{hora_ini}",
            'end':   f"{aloc.data}T{hora_fim}",
            'backgroundColor': '#ef4444' if infracoes else base_color,
            'borderColor':     '#b91c1c' if infracoes else base_color,
            'classNames':      ['fc-evento-clt'] if infracoes else [],
            'extendedProps': {
                'func_id':    str(aloc.funcionario_id),
                'func_nome':  aloc.funcionario.nome,
                'turno_id':   aloc.turno_id,
                'turno_nome': aloc.turno.nome,
                'turno_color': base_color,
                'hora_inicio': hora_ini,
                'hora_fim':    hora_fim,
                'infracoes':   [i['message'] for i in infracoes],
                'aloc_id':     aloc.id,
                'warning':     aloc.compliance_warning,
            },
        })
    return jsonify(events)


@escalas_bp.route('/alocar/<int:aloc_id>/mover', methods=['PATCH'])
@login_required
def alocar_mover(aloc_id):
    """Drag & drop: move alocação para nova data com validação CLT."""
    aloc = AlocacaoDiaria.query.get_or_404(aloc_id)
    data_json = request.get_json(force=True) or {}
    try:
        nova_data = date.fromisoformat(data_json.get('data', ''))
    except ValueError:
        return jsonify({'ok': False, 'error': 'Data inválida'}), 400

    infracoes = validar_alocacao(aloc.funcionario_id, nova_data, aloc.turno)
    if infracoes:
        return jsonify({'ok': False, 'infracoes': infracoes}), 422

    aloc.data = nova_data
    db.session.commit()
    return jsonify({'ok': True})


@escalas_bp.route('/alocar/<int:aloc_id>/excluir', methods=['DELETE'])
@login_required
def alocar_excluir(aloc_id):
    aloc = AlocacaoDiaria.query.get_or_404(aloc_id)
    db.session.delete(aloc)
    db.session.commit()
    return jsonify({'ok': True})


# ── Phase 1B: Resource Scheduler endpoints ────────────────────────────────────

@escalas_bp.route('/resources')
@login_required
def resources():
    """Retorna funcionários como recursos para FullCalendar Resource Timeline."""
    dept    = request.args.get('dept', '').strip()
    func_id = request.args.get('func_id', '').strip()
    q = Funcionario.query.filter_by(ativo=True)
    q = _filtrar_dept(q, dept)
    if func_id: q = q.filter(Funcionario.id == func_id)
    funcs = q.order_by(Funcionario.departamento, Funcionario.nome).all()
    return jsonify([{
        'id':    str(f.id),
        'title': f.nome,
        'extendedProps': {
            'departamento': f.departamento or '—',
            'funcao': f.funcao or '—',
        },
    } for f in funcs])


@escalas_bp.route('/alocar-ajax', methods=['POST'])
@login_required
def alocar_ajax():
    """Cria alocação via drag-and-drop do sidebar (turno externo → recurso/data)."""
    data_json = request.get_json(force=True) or {}
    try:
        func_id  = str(data_json['funcionario_id'])
        turno_id = int(data_json['turno_id'])
        nova_data = date.fromisoformat(data_json['data'][:10])
    except (KeyError, ValueError):
        return jsonify({'ok': False, 'error': 'Dados inválidos'}), 400

    turno = Turno.query.get_or_404(turno_id)
    infracoes = validar_alocacao(func_id, nova_data, turno)

    # Infrações bloqueantes vs. warnings
    bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
    warnings    = [i for i in infracoes if i.get('severity', 'error') != 'error']

    if bloqueantes and not data_json.get('force'):
        return jsonify({'ok': False, 'infracoes': bloqueantes, 'warnings': warnings}), 422

    aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=nova_data).first()
    warning_txt = '; '.join(i['message'] for i in warnings) or None
    if aloc:
        aloc.turno_id = turno_id
        aloc.compliance_warning = warning_txt
    else:
        aloc = AlocacaoDiaria(funcionario_id=func_id, turno_id=turno_id,
                               data=nova_data, compliance_warning=warning_txt)
        db.session.add(aloc)
    db.session.commit()
    return jsonify({'ok': True, 'aloc_id': aloc.id, 'warnings': warnings})


@escalas_bp.route('/alocar/<int:aloc_id>/trocar-recurso', methods=['PATCH'])
@login_required
def alocar_trocar_recurso(aloc_id):
    """Move alocação para outro funcionário (drag entre linhas no resource view)."""
    aloc = AlocacaoDiaria.query.get_or_404(aloc_id)
    data_json = request.get_json(force=True) or {}
    try:
        novo_func_id = str(data_json['funcionario_id'])
        nova_data    = date.fromisoformat(data_json.get('data', str(aloc.data))[:10])
    except (KeyError, ValueError):
        return jsonify({'ok': False, 'error': 'Dados inválidos'}), 400

    infracoes = validar_alocacao(novo_func_id, nova_data, aloc.turno)
    bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
    if bloqueantes and not data_json.get('force'):
        return jsonify({'ok': False, 'infracoes': bloqueantes}), 422

    aloc.funcionario_id = novo_func_id
    aloc.data = nova_data
    aloc.compliance_warning = '; '.join(i['message'] for i in infracoes) or None
    db.session.commit()
    return jsonify({'ok': True})


@escalas_bp.route('/scheduler')
@login_required
def scheduler():
    """Resource Timeline (Gantt-style schedule per employee)."""
    departamentos = _departamentos()
    turnos = Turno.query.order_by(Turno.nome).all()
    funcionarios = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    return render_template('escalas/scheduler.html',
                           departamentos=departamentos,
                           turnos=turnos,
                           funcionarios=funcionarios)


# ── Phase 4: Gantt planejado vs. realizado ────────────────────────────────────

@escalas_bp.route('/gantt')
@login_required
def gantt():
    """Visão Gantt do dia: planejado (AlocacaoDiaria) vs. realizado (Batida)."""
    data_str = request.args.get('data', date.today().strftime('%Y-%m-%d'))
    try:
        data_ref = date.fromisoformat(data_str)
    except ValueError:
        data_ref = date.today()

    dept    = request.args.get('dept', '').strip()
    func_id = request.args.get('func_id', '').strip()

    departamentos = _departamentos()
    funcionarios  = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()

    return render_template('escalas/gantt.html',
                           data=data_str,
                           data_ref=data_ref,
                           departamentos=departamentos,
                           funcionarios=funcionarios,
                           dept_sel=dept,
                           func_sel=func_id)


@escalas_bp.route('/gantt/dados')
@login_required
def gantt_dados():
    """JSON: linhas do Gantt para a data e filtros selecionados."""
    data_str = request.args.get('data', date.today().strftime('%Y-%m-%d'))
    dept     = request.args.get('dept', '').strip()
    func_id  = request.args.get('func_id', '').strip()

    try:
        data_ref = date.fromisoformat(data_str)
    except ValueError:
        return jsonify([])

    q = (
        AlocacaoDiaria.query
        .filter_by(data=data_ref)
        .join(Turno)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
    )
    q = _filtrar_dept(q, dept)
    if func_id: q = q.filter(AlocacaoDiaria.funcionario_id == func_id)
    alocacoes = q.order_by(Funcionario.nome).all()

    # Pega todas as batidas do dia (agrupadas por funcionário)
    batidas_q = Batida.query.filter_by(data=data_ref)
    if func_id: batidas_q = batidas_q.filter_by(funcionario_id=func_id)
    batidas_por_func = {}
    for b in batidas_q.all():
        batidas_por_func.setdefault(b.funcionario_id, []).append(b.hora)

    rows = []
    for aloc in alocacoes:
        h_ini, h_fim, _ = aloc.turno.get_horario_dia(data_ref.weekday())
        batidas = sorted(batidas_por_func.get(aloc.funcionario_id, []))
        rows.append({
            'funcionario_id':   str(aloc.funcionario_id),
            'funcionario_nome': aloc.funcionario.nome,
            'departamento':     aloc.funcionario.departamento or '—',
            'turno_nome':       aloc.turno.nome,
            'turno_color':      aloc.turno.color or '#4f46e5',
            'planejado_inicio': h_ini.strftime('%H:%M'),
            'planejado_fim':    h_fim.strftime('%H:%M'),
            'batidas':          batidas,
            'presente':         bool(batidas),
            'compliance_warning': aloc.compliance_warning,
        })
    return jsonify(rows)


_DIAS_PT = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']


def _departamentos():
    """Lista de departamentos individuais + grupos (para dropdowns em todo o sistema)."""
    rows = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    depts = [r[0] for r in rows]
    grupos = [g.nome for g in GrupoDepartamento.query.order_by(GrupoDepartamento.nome).all()]
    # Grupos primeiro, depois departamentos individuais
    return grupos + depts


def _resolver_depts(dept_str: str) -> list:
    """Converte um nome de departamento OU grupo em lista de departamentos reais.
    Ex: 'Praia do Canto' → ['PRAIA FITNESS', 'FUNCIONAL DA PRAIA']
        'PRAIA FITNESS'  → ['PRAIA FITNESS']
        ''               → []   (sem filtro)
    """
    if not dept_str:
        return []
    grupo = GrupoDepartamento.query.filter_by(nome=dept_str).first()
    if grupo:
        return grupo.departamentos
    return [dept_str]


def _filtrar_dept(q, dept_str: str):
    """Aplica filtro de departamento a uma query de Funcionario, suportando grupos."""
    depts = _resolver_depts(dept_str)
    if not depts:
        return q
    if len(depts) == 1:
        return q.filter(Funcionario.departamento == depts[0])
    return q.filter(Funcionario.departamento.in_(depts))


def _funcoes(departamento=None):
    """Lista de funções (cargo) distintas, opcionalmente filtradas por departamento."""
    q = (
        db.session.query(Funcionario.funcao)
        .filter(Funcionario.ativo == True, Funcionario.funcao.isnot(None))
    )
    if departamento:
        depts = _resolver_depts(departamento)
        if len(depts) == 1:
            q = q.filter(Funcionario.departamento == depts[0])
        elif depts:
            q = q.filter(Funcionario.departamento.in_(depts))
    return [r[0] for r in q.distinct().order_by(Funcionario.funcao).all()]


@escalas_bp.route('/cargo-mensal', methods=['GET', 'POST'])
@login_required
def cargo_mensal():
    """Bulk allocation: define turno para uma função/cargo em um mês inteiro."""
    departamentos = _departamentos()
    turnos = Turno.query.order_by(Turno.nome).all()

    # Funções disponíveis (pode filtrar por departamento via AJAX)
    dept_sel = request.args.get('dept', '') or request.form.get('departamento', '')
    funcoes = _funcoes(dept_sel or None)

    if request.method == 'POST':
        departamento = request.form.get('departamento', '').strip()
        funcao       = request.form.get('funcao', '').strip()
        turno_id     = int(request.form['turno_id'])
        mes_ano      = request.form['mes_ano']   # "YYYY-MM"

        try:
            ano, mes = int(mes_ano[:4]), int(mes_ano[5:7])
        except (ValueError, IndexError):
            flash('Mês inválido.', 'danger')
            return redirect(url_for('escalas.cargo_mensal'))

        import calendar
        _, dias_no_mes = calendar.monthrange(ano, mes)
        data_ini = date(ano, mes, 1)
        data_fim = date(ano, mes, dias_no_mes)

        turno = Turno.query.get_or_404(turno_id)
        dias_turno = turno.dias_semana_list

        # Funcionários que batem nos critérios
        q = Funcionario.query.filter_by(ativo=True)
        q = _filtrar_dept(q, departamento)
        if funcao:
            q = q.filter(Funcionario.funcao == funcao)
        funcionarios = q.all()

        if not funcionarios:
            flash('Nenhum funcionário encontrado com os critérios informados.', 'warning')
            return redirect(url_for('escalas.cargo_mensal'))

        gerados = 0
        for func in funcionarios:
            d = data_ini
            while d <= data_fim:
                if d.weekday() in dias_turno:
                    aloc = AlocacaoDiaria.query.filter_by(
                        funcionario_id=func.id, data=d).first()
                    if aloc:
                        aloc.turno_id = turno_id
                    else:
                        db.session.add(AlocacaoDiaria(
                            funcionario_id=func.id, turno_id=turno_id, data=d))
                    gerados += 1
                d += timedelta(days=1)
        db.session.commit()

        flash(
            f'Escala aplicada! {len(funcionarios)} funcionário(s) × {gerados // len(funcionarios)} dias '
            f'= {gerados} alocações geradas/atualizadas.',
            'success'
        )
        return redirect(url_for('escalas.cargo_mensal'))

    # Mês padrão = mês atual
    mes_atual = date.today().strftime('%Y-%m')
    return render_template('escalas/cargo_mensal.html',
                           departamentos=departamentos,
                           funcoes=funcoes,
                           turnos=turnos,
                           mes_atual=mes_atual,
                           dept_sel=dept_sel)


@escalas_bp.route('/cargo-mensal/funcoes')
@login_required
def cargo_mensal_funcoes():
    """AJAX: retorna lista de funções filtradas por departamento."""
    dept = request.args.get('dept', '').strip()
    return jsonify(_funcoes(dept or None))


@escalas_bp.route('/cargo-mensal/preview')
@login_required
def cargo_mensal_preview():
    """AJAX: retorna funcionários afetados pelos filtros dept+funcao."""
    dept   = request.args.get('dept', '').strip()
    funcao = request.args.get('funcao', '').strip()
    q = Funcionario.query.filter_by(ativo=True)
    q = _filtrar_dept(q, dept)
    if funcao: q = q.filter(Funcionario.funcao == funcao)
    funcionarios = q.order_by(Funcionario.nome).all()
    return jsonify([{
        'nome': f.nome,
        'funcao': f.funcao,
        'departamento': f.departamento,
    } for f in funcionarios])


@escalas_bp.route('/funcionario/<func_id>/proximos')
@login_required
def proximos_turnos(func_id):
    """Próximos 7 dias de escala de um funcionário (usada no modal de detalhes)."""
    from datetime import timedelta
    hoje = date.today()
    fim  = hoje + timedelta(days=7)
    alocacoes = (
        AlocacaoDiaria.query
        .filter(
            AlocacaoDiaria.funcionario_id == func_id,
            AlocacaoDiaria.data >= hoje,
            AlocacaoDiaria.data <= fim,
        )
        .join(Turno)
        .order_by(AlocacaoDiaria.data)
        .all()
    )
    hoje_aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=hoje).first()

    dias_com_aloc = {a.data for a in alocacoes}
    proxima_folga = None
    for i in range(9):
        d = hoje + timedelta(days=i)
        if d not in dias_com_aloc:
            proxima_folga = f"{_DIAS_PT[d.weekday()]} {d.strftime('%d/%m')}"
            break

    return jsonify({
        'turno_hoje': {
            'nome':   hoje_aloc.turno.nome,
            'inicio': hoje_aloc.turno.hora_inicio.strftime('%H:%M'),
            'fim':    hoje_aloc.turno.hora_fim.strftime('%H:%M'),
        } if hoje_aloc else None,
        'proxima_folga': proxima_folga,
        'proximos': [{
            'data':   a.data.strftime('%d/%m'),
            'dia':    _DIAS_PT[a.data.weekday()],
            'turno':  a.turno.nome,
            'inicio': a.turno.hora_inicio.strftime('%H:%M'),
            'fim':    a.turno.hora_fim.strftime('%H:%M'),
        } for a in alocacoes],
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Etapa 2 – Heatmap de Cobertura
# ═══════════════════════════════════════════════════════════════════════════════

@escalas_bp.route('/cobertura')
@login_required
def cobertura():
    """Matriz de cobertura mensal: funcionários × dias, rodapé = contagem/função."""
    mes_ano  = request.args.get('mes_ano', date.today().strftime('%Y-%m'))
    dept_sel = request.args.get('dept', '')
    func_sel = request.args.get('funcao', '')
    return render_template(
        'escalas/cobertura.html',
        mes_ano=mes_ano,
        dept_sel=dept_sel,
        func_sel=func_sel,
        departamentos=_departamentos(),
        funcoes=_funcoes(dept_sel or None),
    )


@escalas_bp.route('/cobertura/dados')
@login_required
def cobertura_dados():
    """AJAX: retorna matriz de cobertura para o mês/filtros solicitados."""
    import calendar as cal_mod
    mes_ano = request.args.get('mes_ano', date.today().strftime('%Y-%m'))
    dept    = request.args.get('dept', '').strip()
    funcao  = request.args.get('funcao', '').strip()

    try:
        ano, mes = int(mes_ano[:4]), int(mes_ano[5:7])
    except (ValueError, IndexError):
        return jsonify({'error': 'mes_ano inválido'}), 400

    _, dias_no_mes = cal_mod.monthrange(ano, mes)
    data_ini = date(ano, mes, 1)
    data_fim = date(ano, mes, dias_no_mes)

    # Funcionários filtrados
    q_func = Funcionario.query.filter_by(ativo=True)
    q_func = _filtrar_dept(q_func, dept)
    if funcao: q_func = q_func.filter(Funcionario.funcao == funcao)
    funcionarios = q_func.order_by(Funcionario.nome).all()
    func_ids = [f.id for f in funcionarios]

    if not func_ids:
        return jsonify({'funcionarios': [], 'cobertura': {}, 'dias_no_mes': dias_no_mes, 'ano': ano, 'mes': mes})

    # Alocações do mês
    alocacoes = (
        AlocacaoDiaria.query
        .filter(
            AlocacaoDiaria.funcionario_id.in_(func_ids),
            AlocacaoDiaria.data >= data_ini,
            AlocacaoDiaria.data <= data_fim,
        )
        .join(Turno)
        .all()
    )

    # Indexar por func_id → dia → turno
    aloc_map: dict[str, dict[int, dict]] = {}
    for aloc in alocacoes:
        d = aloc.data.day
        aloc_map.setdefault(aloc.funcionario_id, {})[d] = {
            'turno': aloc.turno.nome,
            'color': aloc.turno.color or '#4f46e5',
            'warning': bool(aloc.compliance_warning),
        }

    # Cobertura por dia (contagem de funcionários escalados)
    cobertura = {}
    for d in range(1, dias_no_mes + 1):
        cobertura[d] = sum(1 for fid in func_ids if d in aloc_map.get(fid, {}))

    # Mapear dias de domingo para highlight
    domingos = {d for d in range(1, dias_no_mes + 1)
                if date(ano, mes, d).weekday() == 6}

    resultado_funcs = []
    for f in funcionarios:
        dias = {}
        for d in range(1, dias_no_mes + 1):
            dias[d] = aloc_map.get(f.id, {}).get(d)  # None se folga
        resultado_funcs.append({
            'id':    f.id,
            'nome':  f.nome,
            'funcao': f.funcao or '',
            'dias':  dias,
        })

    return jsonify({
        'funcionarios': resultado_funcs,
        'cobertura':    cobertura,
        'dias_no_mes':  dias_no_mes,
        'domingos':     list(domingos),
        'ano':  ano,
        'mes':  mes,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Etapa 3 – Padrões de Revezamento
# ═══════════════════════════════════════════════════════════════════════════════

@escalas_bp.route('/padroes')
@login_required
def padroes():
    lista = PadraoTurno.query.order_by(PadraoTurno.nome).all()
    turnos = Turno.query.order_by(Turno.nome).all()
    return render_template('escalas/padroes.html',
                           padroes=lista, turnos=turnos,
                           departamentos=_departamentos(),
                           funcionarios=Funcionario.query.filter_by(ativo=True)
                                         .order_by(Funcionario.nome).all())


@escalas_bp.route('/padroes/novo', methods=['POST'])
@login_required
def padrao_novo():
    nome     = request.form['nome'].strip()
    dias_on  = int(request.form.get('dias_trabalho', 5))
    dias_off = int(request.form.get('dias_folga', 2))
    turno_id = request.form.get('turno_id') or None
    dept     = request.form.get('departamento', '').strip() or None
    descricao = request.form.get('descricao', '').strip() or None

    p = PadraoTurno(nome=nome, dias_trabalho=dias_on, dias_folga=dias_off,
                    turno_id=turno_id, departamento=dept, descricao=descricao)
    db.session.add(p)
    db.session.commit()
    flash(f'Padrão "{nome}" criado.', 'success')
    return redirect(url_for('escalas.padroes'))


@escalas_bp.route('/padroes/<int:padrao_id>/excluir', methods=['POST'])
@login_required
def padrao_excluir(padrao_id):
    p = PadraoTurno.query.get_or_404(padrao_id)
    db.session.delete(p)
    db.session.commit()
    flash('Padrão excluído.', 'success')
    return redirect(url_for('escalas.padroes'))


@escalas_bp.route('/padroes/<int:padrao_id>/aplicar', methods=['POST'])
@login_required
def padrao_aplicar(padrao_id):
    """Aplica o ciclo do padrão a um funcionário em um intervalo de datas."""
    p = PadraoTurno.query.get_or_404(padrao_id)

    func_id    = request.form['funcionario_id']
    data_ini   = date.fromisoformat(request.form['data_inicio'])
    data_fim   = date.fromisoformat(request.form['data_fim'])
    turno_id   = int(request.form.get('turno_id') or p.turno_id or 0)

    if not turno_id:
        flash('Selecione um turno para aplicar o padrão.', 'danger')
        return redirect(url_for('escalas.padroes'))

    turno = Turno.query.get_or_404(turno_id)
    ciclo = p.dias_trabalho + p.dias_folga
    gerados = 0
    d = data_ini
    pos = 0  # posição no ciclo
    while d <= data_fim:
        if pos < p.dias_trabalho:   # dia de trabalho no ciclo
            if d.weekday() in turno.dias_semana_list:
                aloc = AlocacaoDiaria.query.filter_by(
                    funcionario_id=func_id, data=d).first()
                if aloc:
                    aloc.turno_id = turno_id
                else:
                    db.session.add(AlocacaoDiaria(
                        funcionario_id=func_id, turno_id=turno_id, data=d))
                gerados += 1
        pos = (pos + 1) % ciclo
        d += timedelta(days=1)

    db.session.commit()
    flash(f'Padrão aplicado: {gerados} alocações geradas/atualizadas.', 'success')
    return redirect(url_for('escalas.padroes'))


# ═══════════════════════════════════════════════════════════════════════════════
# Etapa 4 – Auto-Solver & Alertas de Conflito
# ═══════════════════════════════════════════════════════════════════════════════

@escalas_bp.route('/alertas')
@login_required
def alertas():
    """AJAX: dias descobertos + violações Art. 386 no mês."""
    from services.solver_escala import alertas_cobertura, violacoes_art386
    mes_ano = request.args.get('mes_ano', date.today().strftime('%Y-%m'))
    dept    = request.args.get('dept', '').strip() or None
    funcao  = request.args.get('funcao', '').strip() or None

    descobertos = alertas_cobertura(mes_ano, dept=dept, funcao=funcao)
    art386      = violacoes_art386(mes_ano, dept=dept, funcao=funcao)
    return jsonify({'descobertos': descobertos, 'art386': art386})


@escalas_bp.route('/sugerir-cobertura')
@login_required
def sugerir_cobertura_view():
    """AJAX: sugere o melhor substituto para cobrir um dia/função."""
    from services.solver_escala import sugerir_substituto
    data_str = request.args.get('data', '')
    funcao   = request.args.get('funcao', '').strip()
    dept     = request.args.get('dept', '').strip() or None
    try:
        data_ref = date.fromisoformat(data_str)
    except ValueError:
        return jsonify({'error': 'data inválida'}), 400
    sugestao = sugerir_substituto(data_ref, funcao=funcao, dept=dept)
    return jsonify(sugestao or {'error': 'Nenhum candidato disponível'})


@escalas_bp.route('/aplicar-sugestao', methods=['POST'])
@login_required
def aplicar_sugestao():
    """Aplica a sugestão do solver: cria a alocação do substituto."""
    data = request.get_json(force=True) or {}
    func_id  = data.get('func_id')
    turno_id = data.get('turno_id')
    data_str = data.get('data')
    try:
        data_aloc = date.fromisoformat(data_str)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'data inválida'}), 400

    turno = Turno.query.get_or_404(turno_id)
    from services.motor_clt import validar_alocacao
    infracoes = validar_alocacao(func_id, data_aloc, turno)
    bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
    if bloqueantes and not data.get('force'):
        return jsonify({'ok': False, 'infracoes': bloqueantes}), 422

    aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=data_aloc).first()
    if aloc:
        aloc.turno_id = turno_id
    else:
        db.session.add(AlocacaoDiaria(funcionario_id=func_id, turno_id=turno_id, data=data_aloc))
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════════════════
# Grupos de Departamentos – CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@escalas_bp.route('/grupos')
@login_required
def grupos():
    """Gerenciar grupos de unidades (ex: Praia do Canto → PRAIA FITNESS + FUNCIONAL)."""
    lista = GrupoDepartamento.query.order_by(GrupoDepartamento.nome).all()
    # Departamentos individuais disponíveis
    rows = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    depts_individuais = [r[0] for r in rows]
    return render_template('escalas/grupos.html',
                           grupos=lista, depts=depts_individuais)


@escalas_bp.route('/grupos/novo', methods=['POST'])
@login_required
def grupo_novo():
    nome  = request.form.get('nome', '').strip()
    depts = request.form.getlist('departamentos')
    if not nome or not depts:
        flash('Informe nome e ao menos um departamento.', 'danger')
        return redirect(url_for('escalas.grupos'))
    if GrupoDepartamento.query.filter_by(nome=nome).first():
        flash(f'Grupo "{nome}" já existe.', 'warning')
        return redirect(url_for('escalas.grupos'))
    g = GrupoDepartamento(nome=nome)
    g.departamentos = depts
    db.session.add(g)
    db.session.commit()
    flash(f'Grupo "{nome}" criado com {len(depts)} unidade(s).', 'success')
    return redirect(url_for('escalas.grupos'))


@escalas_bp.route('/grupos/<int:grupo_id>/editar', methods=['POST'])
@login_required
def grupo_editar(grupo_id):
    g = GrupoDepartamento.query.get_or_404(grupo_id)
    g.nome = request.form.get('nome', g.nome).strip()
    g.departamentos = request.form.getlist('departamentos')
    db.session.commit()
    flash(f'Grupo "{g.nome}" atualizado.', 'success')
    return redirect(url_for('escalas.grupos'))


@escalas_bp.route('/grupos/<int:grupo_id>/excluir', methods=['POST'])
@login_required
def grupo_excluir(grupo_id):
    g = GrupoDepartamento.query.get_or_404(grupo_id)
    db.session.delete(g)
    db.session.commit()
    flash('Grupo excluído.', 'success')
    return redirect(url_for('escalas.grupos'))


@escalas_bp.route('/grupos/api')
@login_required
def grupos_api():
    """AJAX: retorna todos os grupos com seus departamentos (para uso em outros formulários)."""
    grupos = GrupoDepartamento.query.order_by(GrupoDepartamento.nome).all()
    return jsonify([{
        'nome': g.nome,
        'departamentos': g.departamentos,
    } for g in grupos])
