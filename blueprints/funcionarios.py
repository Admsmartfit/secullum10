import json
from flask import Blueprint, render_template
from flask_login import login_required
from models import Funcionario
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
            'score': scores.get(f.id),
        }

    return render_template(
        'funcionarios.html',
        funcionarios=all_funcs,
        scores=scores,
        funcionarios_json=json.dumps(funcionarios_dict),
    )
