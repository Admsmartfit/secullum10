from datetime import date, datetime, timedelta
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Turno, AlocacaoDiaria, Funcionario, Batida
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

        # Validação CLT
        infracoes = validar_alocacao(func_id, data_aloc, turno)
        if infracoes:
            return jsonify({'ok': False, 'infracoes': infracoes}), 422

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
            else:
                aloc = AlocacaoDiaria(funcionario_id=func_id, turno_id=turno_id, data=data_aloc)
                db.session.add(aloc)
            db.session.commit()
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'ok': True, 'message': 'Alocação salva!'})
            flash('Alocação salva com sucesso!', 'success')

        return redirect(url_for('escalas.alocar'))

    # Pré-selecionar data
    data_sel = request.args.get('data', date.today().strftime('%Y-%m-%d'))
    return render_template('escalas/alocar.html',
                           funcionarios=funcionarios, turnos=turnos, data_sel=data_sel)


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
    if dept:
        q = q.filter(Funcionario.departamento == dept)
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

        events.append({
            'id': aloc.id,
            'title': titulo,
            'start': f"{aloc.data}T{hora_ini}",
            'end':   f"{aloc.data}T{hora_fim}",
            'backgroundColor': '#ef4444' if infracoes else '#4f46e5',
            'borderColor':     '#b91c1c' if infracoes else '#4338ca',
            'extendedProps': {
                'func_id':    str(aloc.funcionario_id),
                'func_nome':  aloc.funcionario.nome,
                'turno_id':   aloc.turno_id,
                'turno_nome': aloc.turno.nome,
                'hora_inicio': hora_ini,
                'hora_fim':    hora_fim,
                'infracoes':   [i['message'] for i in infracoes],
                'aloc_id':     aloc.id,
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


_DIAS_PT = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']


def _departamentos():
    """Lista de departamentos distintos dos funcionários ativos."""
    rows = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    return [r[0] for r in rows]


def _funcoes(departamento=None):
    """Lista de funções (cargo) distintas, opcionalmente filtradas por departamento."""
    q = (
        db.session.query(Funcionario.funcao)
        .filter(Funcionario.ativo == True, Funcionario.funcao.isnot(None))
    )
    if departamento:
        q = q.filter(Funcionario.departamento == departamento)
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
        if departamento:
            q = q.filter(Funcionario.departamento == departamento)
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
    if dept:   q = q.filter(Funcionario.departamento == dept)
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
