from datetime import date, datetime
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
        turno = Turno(
            nome=request.form['nome'].strip(),
            hora_inicio=datetime.strptime(request.form['hora_inicio'], '%H:%M').time(),
            hora_fim=datetime.strptime(request.form['hora_fim'], '%H:%M').time(),
            dias_semana=','.join(dias) if dias else '0,1,2,3,4',
        )
        db.session.add(turno)
        db.session.commit()
        flash(f'Turno "{turno.nome}" criado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=None, dias=DIAS_SEMANA)


@escalas_bp.route('/turno/<int:turno_id>/editar', methods=['GET', 'POST'])
@login_required
def turno_editar(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    if request.method == 'POST':
        dias = request.form.getlist('dias_semana')
        turno.nome = request.form['nome'].strip()
        turno.hora_inicio = datetime.strptime(request.form['hora_inicio'], '%H:%M').time()
        turno.hora_fim = datetime.strptime(request.form['hora_fim'], '%H:%M').time()
        turno.dias_semana = ','.join(dias) if dias else '0,1,2,3,4'
        db.session.commit()
        flash(f'Turno "{turno.nome}" atualizado!', 'success')
        return redirect(url_for('escalas.index'))
    return render_template('escalas/turno_form.html', turno=turno, dias=DIAS_SEMANA)


@escalas_bp.route('/turno/<int:turno_id>/excluir', methods=['POST'])
@login_required
def turno_excluir(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    db.session.delete(turno)
    db.session.commit()
    flash(f'Turno "{turno.nome}" excluído.', 'warning')
    return redirect(url_for('escalas.index'))


# ── Alocações ─────────────────────────────────────────────────────────────────

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

        # Upsert alocação
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
