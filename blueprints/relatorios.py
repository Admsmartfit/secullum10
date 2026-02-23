from datetime import datetime, date
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import Batida, Funcionario

relatorios_bp = Blueprint('relatorios', __name__)


@relatorios_bp.route('/relatorios')
@login_required
def relatorios():
    data_inicio_str = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))

    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()

    batidas_query = (
        Batida.query
        .filter(Batida.data >= data_inicio, Batida.data <= data_fim)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )

    total_batidas = len(batidas_query)
    funcionarios_unicos = len(set(b.funcionario_id for b in batidas_query))
    inconsistencias = sum(1 for b in batidas_query if b.inconsistente)

    por_departamento = {}
    por_funcionario = {}
    for b in batidas_query:
        dept = b.funcionario.departamento or 'Sem Departamento'
        por_departamento[dept] = por_departamento.get(dept, 0) + 1

        fid = b.funcionario_id
        if fid not in por_funcionario:
            por_funcionario[fid] = {'nome': b.funcionario.nome, 'batidas': 0}
        por_funcionario[fid]['batidas'] += 1

    ranking = sorted(por_funcionario.values(), key=lambda x: x['batidas'], reverse=True)[:10]

    return render_template(
        'relatorios.html',
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        total_batidas=total_batidas,
        funcionarios_unicos=funcionarios_unicos,
        inconsistencias=inconsistencias,
        por_departamento=por_departamento,
        ranking=ranking,
    )
