from datetime import date, datetime, timedelta
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from extensions import db
from models import Turno, AlocacaoDiaria, Funcionario, Batida, GrupoDepartamento
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
            funcao=request.form.get('funcao', '').strip() or None,
            color=request.form.get('color', '#4f46e5') or '#4f46e5',
            tipo_turno=request.form.get('tipo_turno') or None,
        )
        db.session.add(turno)
        db.session.commit()
        flash(f'Turno "{turno.nome}" criado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=None, dias=DIAS_SEMANA,
                           departamentos=_departamentos(), funcoes=_funcoes())


def _funcoes():
    """Retorna lista de todas as funções (cargos) únicas dos funcionários."""
    res = db.session.query(Funcionario.funcao).filter(Funcionario.funcao.isnot(None)).distinct().all()
    return sorted([r[0] for r in res if r[0]])


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
        turno.funcao = request.form.get('funcao', '').strip() or None
        turno.color = request.form.get('color', '#4f46e5') or '#4f46e5'
        turno.tipo_turno = request.form.get('tipo_turno') or None
        db.session.commit()
        flash(f'Turno "{turno.nome}" atualizado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=turno, dias=DIAS_SEMANA,
                           departamentos=_departamentos(), funcoes=_funcoes())


@escalas_bp.route('/turno/<int:turno_id>/excluir', methods=['POST'])
@login_required
def turno_excluir(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    db.session.delete(turno)
    db.session.commit()
    flash(f'Turno "{turno.nome}" excluído.', 'warning')
    return redirect(url_for('escalas.index'))


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

    # Pegamos TODOS os funcionários se não houver filtro, para poder mostrar o Horário Base
    funcs_q = Funcionario.query.filter_by(ativo=True)
    funcs_q = _filtrar_dept(funcs_q, dept)
    if func_id: funcs_q = funcs_q.filter(Funcionario.id == func_id)
    if funcao: funcs_q = funcs_q.filter(Funcionario.funcao == funcao)
    
    funcionarios = funcs_q.all()
    func_ids = [f.id for f in funcionarios]

    # Pegamos as alocações (exceções) no período
    alocacoes_raw = (
        AlocacaoDiaria.query
        .filter(AlocacaoDiaria.data >= d_ini, AlocacaoDiaria.data <= d_fim)
        .filter(AlocacaoDiaria.funcionario_id.in_(func_ids))
        .all()
    )
    # Mapa de exceções {data_iso: {func_id: aloc}}
    excecoes_map = {}
    for aloc in alocacoes_raw:
        data_str = aloc.data.isoformat()
        excecoes_map.setdefault(data_str, {})[aloc.funcionario_id] = aloc

    events = []
    curr_d = d_ini
    while curr_d <= d_fim:
        data_iso = curr_d.isoformat()
        for f in funcionarios:
            aloc = excecoes_map.get(data_iso, {}).get(f.id)
            turno = None
            is_excecao = False
            
            if aloc:
                turno = aloc.turno
                is_excecao = True
            elif f.horario_base:
                # Fallback para o Horário Base se estiver no dia da semana configurado
                if curr_d.weekday() in f.horario_base.dias_semana_list:
                    turno = f.horario_base
            
            if not turno:
                continue

            infracoes = validar_alocacao(f.id, curr_d, turno)
            h_ini_t, h_fim_t, _ = turno.get_horario_dia(curr_d.weekday())
            
            nome_parts = f.nome.split()
            titulo = nome_parts[0] + (f' {nome_parts[1][0]}.' if len(nome_parts) > 1 else '')
            
            base_color = turno.color or '#4f46e5'
            events.append({
                'id': aloc.id if aloc else f"base_{f.id}_{data_iso}",
                'resourceId': str(f.id),
                'title': titulo,
                'start': f"{data_iso}T{h_ini_t.strftime('%H:%M')}",
                'end':   f"{data_iso}T{h_fim_t.strftime('%H:%M')}",
                'backgroundColor': '#ef4444' if infracoes else base_color,
                'borderColor':     '#000000' if is_excecao else base_color, # Borda preta p/ exceção
                'classNames':      (['fc-evento-clt'] if infracoes else []) + (['fc-excecao'] if is_excecao else []),
                'extendedProps': {
                    'func_id':    str(f.id),
                    'func_nome':  f.nome,
                    'turno_id':   turno.id,
                    'turno_nome': turno.nome,
                    'is_excecao': is_excecao,
                    'infracoes':   [i['message'] for i in infracoes],
                    'aloc_id':     aloc.id if aloc else None,
                },
            })
        curr_d += timedelta(days=1)
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


@escalas_bp.route('/cargo-mensal/funcoes')
@login_required
def cargo_mensal_funcoes():
    """AJAX: retorna lista de funções filtradas por departamento."""
    dept = request.args.get('dept', '').strip()
    return jsonify(_funcoes(dept or None))


@escalas_bp.route('/funcionario/<func_id>/proximos')
@login_required
def proximos_turnos(func_id):
    """Próximos dias de escala — usa AlocacaoDiaria (exceção) ou horario_base (padrão)."""
    func = Funcionario.query.get_or_404(func_id)
    hoje = date.today()

    # Exceções explícitas dos próximos 10 dias
    excecoes = {
        a.data: a.turno
        for a in AlocacaoDiaria.query
        .filter(
            AlocacaoDiaria.funcionario_id == func_id,
            AlocacaoDiaria.data >= hoje,
            AlocacaoDiaria.data <= hoje + timedelta(days=10),
        )
        .join(Turno)
        .all()
    }

    def _turno_efetivo(d):
        """Retorna (turno, is_excecao) para o dia d, ou (None, False) se folga."""
        if d in excecoes:
            return excecoes[d], True
        if func.horario_base and d.weekday() in func.horario_base.dias_semana_list:
            return func.horario_base, False
        return None, False

    # Turno de hoje
    turno_hoje, _ = _turno_efetivo(hoje)
    if turno_hoje:
        h_ini, h_fim, _ = turno_hoje.get_horario_dia(hoje.weekday())
        turno_hoje_data = {
            'nome':   turno_hoje.nome,
            'inicio': h_ini.strftime('%H:%M'),
            'fim':    h_fim.strftime('%H:%M'),
        }
    else:
        turno_hoje_data = None

    # Próxima folga (primeiro dia sem turno nos próximos 10 dias, pulando hoje)
    proxima_folga = None
    for i in range(1, 11):
        d = hoje + timedelta(days=i)
        if _turno_efetivo(d)[0] is None:
            proxima_folga = f"{_DIAS_PT[d.weekday()]} {d.strftime('%d/%m')}"
            break

    # Próximos 7 dias com turno (excluindo hoje)
    proximos = []
    for i in range(1, 8):
        d = hoje + timedelta(days=i)
        t, is_exc = _turno_efetivo(d)
        if t:
            h_ini, h_fim, _ = t.get_horario_dia(d.weekday())
            proximos.append({
                'data':      d.strftime('%d/%m'),
                'dia':       _DIAS_PT[d.weekday()],
                'turno':     t.nome,
                'inicio':    h_ini.strftime('%H:%M'),
                'fim':       h_fim.strftime('%H:%M'),
                'excecao':   is_exc,
            })

    return jsonify({
        'turno_hoje':    turno_hoje_data,
        'proxima_folga': proxima_folga,
        'proximos':      proximos,
    })


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


# ═══════════════════════════════════════════════════════════════════════════════
# Quadro Multi-Painéis (Drag-and-Drop)
# ═══════════════════════════════════════════════════════════════════════════════

@escalas_bp.route('/quadro')
@login_required
def quadro():
    """Visualização multi-painel com drag-and-drop de turnos."""
    funcionarios = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    # Mapa {nome_grupo: [dept1, dept2, ...]} para o JS filtrar corretamente
    grupos_map = {
        g.nome: g.departamentos
        for g in GrupoDepartamento.query.order_by(GrupoDepartamento.nome).all()
    }
    return render_template(
        'escalas/quadro.html',
        funcionarios=funcionarios,
        departamentos=_departamentos(),
        turnos=Turno.query.order_by(Turno.nome).all(),
        mes_atual=date.today().strftime('%Y-%m'),
        grupos_map=grupos_map,
    )


@escalas_bp.route('/quadro/dados')
@login_required
def quadro_dados():
    """AJAX: dados de um painel (funcionários × dias) com turno_id para drag-and-drop."""
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

    func_id_param = request.args.get('func_id', '').strip()

    # Sempre busca TODOS os funcionários do dept/funcao (para cobertura_tipos global).
    # Quando func_id_param passado, o resultado de 'funcionarios' é filtrado,
    # mas cobertura_tipos usa todos.
    q_todos = Funcionario.query.filter_by(ativo=True)
    q_todos = _filtrar_dept(q_todos, dept)
    if funcao:
        q_todos = q_todos.filter(Funcionario.funcao == funcao)
    todos_funcionarios = q_todos.order_by(Funcionario.nome).all()
    todos_ids = [f.id for f in todos_funcionarios]

    if func_id_param:
        funcionarios = [f for f in todos_funcionarios if f.id == func_id_param]
    else:
        funcionarios = todos_funcionarios
    func_ids = [f.id for f in funcionarios]

    if not todos_ids:
        return jsonify({
            'funcionarios': [], 'cobertura': {}, 'cobertura_tipos': {},
            'domingo_counts': {}, 'dias_no_mes': dias_no_mes,
            'ano': ano, 'mes': mes, 'sabados': [], 'domingos': [],
        })

    # Busca alocações para TODOS do dept/funcao (para cobertura_tipos)
    alocacoes_todas = (
        AlocacaoDiaria.query
        .filter(
            AlocacaoDiaria.funcionario_id.in_(todos_ids),
            AlocacaoDiaria.data >= data_ini,
            AlocacaoDiaria.data <= data_fim,
        )
        .join(Turno)
        .all()
    )

    aloc_map: dict = {}  # {func_id: {day_int: info_dict}}
    for aloc in alocacoes_todas:
        d = aloc.data.day
        aloc_map.setdefault(aloc.funcionario_id, {})[d] = {
            'turno_id':   aloc.turno_id,
            'turno':      aloc.turno.nome,
            'color':      aloc.turno.color or '#4f46e5',
            'warning':    bool(aloc.compliance_warning),
            'tipo_turno': aloc.turno.tipo_turno,
        }

    sabados  = [d for d in range(1, dias_no_mes + 1) if date(ano, mes, d).weekday() == 5]
    domingos = [d for d in range(1, dias_no_mes + 1) if date(ano, mes, d).weekday() == 6]

    # ── resultado_funcs (apenas os funcionários solicitados) ──────────────────
    resultado_funcs = []
    for f in funcionarios:
        dias = {}
        for d in range(1, dias_no_mes + 1):
            aloc_data = aloc_map.get(f.id, {}).get(d)
            if aloc_data:
                dias[str(d)] = aloc_data
            elif f.horario_base:
                data_ref = date(ano, mes, d)
                if data_ref.weekday() in f.horario_base.dias_semana_list:
                    dias[str(d)] = {
                        'turno_id':   f.horario_base.id,
                        'turno':      f.horario_base.nome,
                        'color':      f.horario_base.color or '#4f46e5',
                        'warning':    False,
                        'base':       True,
                        'tipo_turno': f.horario_base.tipo_turno,
                    }
                else:
                    dias[str(d)] = None
            else:
                dias[str(d)] = None

        resultado_funcs.append({
            'id':     f.id,
            'nome':   f.nome,
            'funcao': f.funcao or '',
            'sexo':   f.sexo or '',
            'dias':   dias,
        })

    # ── cobertura (contagem por dia, todos do dept) ───────────────────────────
    def _dia_info(f, d):
        aloc = aloc_map.get(f.id, {}).get(d)
        if aloc:
            return aloc
        if f.horario_base and date(ano, mes, d).weekday() in f.horario_base.dias_semana_list:
            return {'tipo_turno': f.horario_base.tipo_turno}
        return None

    cobertura = {
        str(d): sum(1 for f in todos_funcionarios if _dia_info(f, d) is not None)
        for d in range(1, dias_no_mes + 1)
    }

    # ── cobertura_tipos: quais tipos A/B/C cobrem cada dia ───────────────────
    cobertura_tipos = {}
    for d in range(1, dias_no_mes + 1):
        tipos = set()
        for f in todos_funcionarios:
            info = _dia_info(f, d)
            if info and info.get('tipo_turno'):
                tipos.add(info['tipo_turno'])
        cobertura_tipos[str(d)] = sorted(tipos)

    # ── domingo_counts: domingos consecutivos por funcionário ─────────────────
    today = date.today()
    days_back_to_sunday = (today.weekday() + 1) % 7  # 0 se hoje é domingo
    last_sunday = today - timedelta(days=days_back_to_sunday)
    sundays_12 = [last_sunday - timedelta(weeks=i) for i in range(12)]

    aloc_sundays_map = {}
    if func_ids:
        for a in AlocacaoDiaria.query.filter(
            AlocacaoDiaria.funcionario_id.in_(func_ids),
            AlocacaoDiaria.data.in_(sundays_12),
        ).all():
            aloc_sundays_map.setdefault(a.funcionario_id, set()).add(a.data)

    domingo_counts = {}
    for f in funcionarios:
        count = 0
        for sun in sundays_12:
            worked = sun in aloc_sundays_map.get(f.id, set())
            if not worked and f.horario_base:
                worked = 6 in f.horario_base.dias_semana_list
            if worked:
                count += 1
            else:
                break
        domingo_counts[f.id] = count

    # ── feriados do mês ───────────────────────────────────────────────────────
    from models import Feriado
    feriados_mes = Feriado.query.filter(
        Feriado.data >= data_ini,
        Feriado.data <= data_fim,
        Feriado.ativo == True,
    ).all()
    feriados = {
        f.data.day: {'descricao': f.descricao or '', 'tipo': f.tipo or ''}
        for f in feriados_mes
    }

    return jsonify({
        'funcionarios':   resultado_funcs,
        'cobertura':      cobertura,
        'cobertura_tipos': cobertura_tipos,
        'domingo_counts': domingo_counts,
        'dias_no_mes':    dias_no_mes,
        'sabados':        sabados,
        'domingos':       domingos,
        'feriados':       feriados,
        'ano':            ano,
        'mes':            mes,
    })


@escalas_bp.route('/quadro/bulk-update', methods=['POST'])
@login_required
def quadro_bulk_update():
    """Salva múltiplas alocações de uma vez (resultado do drag-and-drop)."""
    data = request.get_json(force=True) or {}
    changes = data.get('changes', [])

    saved = 0
    errors = []
    warnings_out = []

    for change in changes:
        try:
            func_id   = str(change['func_id'])
            data_str  = change['data']
            action    = change.get('action', 'set')
            data_aloc = date.fromisoformat(data_str)
        except (KeyError, ValueError) as e:
            errors.append(str(e))
            continue

        if action == 'delete':
            aloc = AlocacaoDiaria.query.filter_by(
                funcionario_id=func_id, data=data_aloc
            ).first()
            if aloc:
                # Havia uma exceção manual → apaga para voltar ao Contrato original
                db.session.delete(aloc)
                saved += 1
            else:
                # Dia sem exceção → estava a mostrar o Horário Base (Contrato).
                # Para forçar folga nesse dia, criamos uma exceção explícita "FOLGA".
                func_obj = db.session.get(Funcionario, func_id)
                if (func_obj and func_obj.horario_base
                        and data_aloc.weekday() in func_obj.horario_base.dias_semana_list):
                    turno_folga = Turno.query.filter_by(nome='FOLGA').first()
                    if not turno_folga:
                        from datetime import time as _time
                        turno_folga = Turno(
                            nome='FOLGA',
                            hora_inicio=_time(0, 0),
                            hora_fim=_time(0, 0),
                            color='#9ca3af',
                        )
                        db.session.add(turno_folga)
                        db.session.flush()
                    db.session.add(AlocacaoDiaria(
                        funcionario_id=func_id,
                        turno_id=turno_folga.id,
                        data=data_aloc,
                    ))
                    saved += 1
            continue

        turno_id = change.get('turno_id')
        if not turno_id:
            continue

        try:
            turno = db.session.get(Turno, turno_id)
        except Exception as e:
            errors.append(f'Erro ao buscar turno {turno_id}: {e}')
            continue
        if not turno:
            errors.append(f'Turno {turno_id} não encontrado')
            continue

        try:
            infracoes = validar_alocacao(func_id, data_aloc, turno)
        except Exception as e:
            errors.append(f'{data_str}: erro de validação CLT: {e}')
            continue
        bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
        avisos      = [i for i in infracoes if i.get('severity') != 'error']

        if bloqueantes and not data.get('force'):
            errors.append(f'{data_str}: {bloqueantes[0]["message"]}')
            continue

        compliance_warn = '; '.join(i['message'] for i in avisos) or None
        if avisos:
            warnings_out.append({
                'data': data_str, 'func_id': func_id, 'message': compliance_warn,
            })

        aloc = AlocacaoDiaria.query.filter_by(
            funcionario_id=func_id, data=data_aloc
        ).first()
        if aloc:
            aloc.turno_id = turno_id
            aloc.compliance_warning = compliance_warn
        else:
            db.session.add(AlocacaoDiaria(
                funcionario_id=func_id, turno_id=turno_id,
                data=data_aloc, compliance_warning=compliance_warn,
            ))
        saved += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500

    return jsonify({'ok': True, 'saved': saved, 'errors': errors, 'warnings': warnings_out})


# ── Grade Mestra (configuração de tipos A/B/C por turno) ─────────────────────

@escalas_bp.route('/grade-mestra')
@login_required
def grade_mestra():
    turnos = Turno.query.order_by(Turno.departamento.nullslast(), Turno.nome).all()
    return render_template('escalas/grade_mestra.html', turnos=turnos)


@escalas_bp.route('/grade-mestra/setar-tipo', methods=['POST'])
@login_required
def grade_mestra_setar_tipo():
    data = request.get_json(force=True) or {}
    turno_id  = data.get('turno_id')
    tipo      = data.get('tipo_turno') or None
    if tipo and tipo not in ('A', 'B', 'C'):
        return jsonify({'ok': False, 'error': 'Tipo inválido'}), 400
    turno = Turno.query.get(turno_id)
    if not turno:
        return jsonify({'ok': False, 'error': 'Turno não encontrado'}), 404
    turno.tipo_turno = tipo
    db.session.commit()
    return jsonify({'ok': True, 'turno_id': turno_id, 'tipo_turno': tipo})

