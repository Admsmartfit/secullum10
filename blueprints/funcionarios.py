import json
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from extensions import db
from models import Funcionario, Turno
from services.score_service import calcular_scores_bulk

funcionarios_bp = Blueprint('funcionarios', __name__)


@funcionarios_bp.route('/funcionarios')
@login_required
def funcionarios():
    all_funcs = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    func_ids = [f.id for f in all_funcs]

    # RF5.5 – Score de pontualidade (últimos 30 dias, bulk)
    scores = calcular_scores_bulk(func_ids) if func_ids else {}

    funcionarios_dict = {}
    for f in all_funcs:
        funcionarios_dict[str(f.id)] = {
            'nome': f.nome or '',
            'cpf': f.cpf or '---',
            'rg': f.rg or '---',
            'pis': f.pis or '---',
            'email': f.email or '---',
            'celular': f.celular or '---',
            'telefone': f.telefone or '---',
            'endereco': f.endereco or '---',
            'bairro': f.bairro or '---',
            'cidade': f.cidade or '---',
            'uf': f.uf or '---',
            'cep': f.cep or '---',
            'departamento': f.departamento or '---',
            'funcao': f.funcao or '---',
            'admissao': f.admissao.strftime('%d/%m/%Y') if f.admissao else '---',
            'nascimento': f.nascimento.strftime('%d/%m/%Y') if f.nascimento else '---',
            'numero_folha': f.numero_folha or '---',
            'horario_base_id': f.horario_base_id,
            'score': scores.get(f.id),
        }

    turnos_data = [
        {'id': t.id, 'nome': t.nome, 'inicio': t.hora_inicio.strftime('%H:%M'), 'fim': t.hora_fim.strftime('%H:%M'), 'funcao': t.funcao}
        for t in Turno.query.order_by(Turno.nome).all()
    ]
    return render_template(
        'funcionarios.html',
        funcionarios=all_funcs,
        scores=scores,
        turnos=turnos_data,
        funcionarios_json=json.dumps(funcionarios_dict),
    )


@funcionarios_bp.route('/funcionarios/<func_id>/set-horario-base', methods=['POST'])
@login_required
def set_horario_base(func_id):
    func = Funcionario.query.get_or_404(func_id)
    turno_id = request.form.get('horario_base_id')
    
    if turno_id == "" or turno_id is None:
        func.horario_base_id = None
    else:
        func.horario_base_id = int(turno_id)
        
    db.session.commit()
    return jsonify({'ok': True, 'message': 'Horário base atualizado com sucesso!'})
