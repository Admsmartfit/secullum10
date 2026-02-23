from flask import Blueprint, render_template
from flask_login import login_required
from datetime import date
from models import Funcionario, Batida, AlocacaoDiaria
from extensions import db
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    today = date.today()
    total_funcionarios = Funcionario.query.filter_by(ativo=True).count()
    batidas_hoje = Batida.query.filter_by(data=today).count()
    inconsistencias = Batida.query.filter_by(data=today, inconsistente=True).count()

    # Funcionários em jornada: têm batida de entrada hoje sem batida de saída correspondente
    # (número ímpar de batidas = alguém ainda dentro)
    subq = (
        db.session.query(Batida.funcionario_id, func.count(Batida.id).label('total'))
        .filter(Batida.data == today)
        .group_by(Batida.funcionario_id)
        .subquery()
    )
    em_jornada = db.session.query(subq).filter(subq.c.total % 2 == 1).count()

    # Ausências: escalados hoje sem nenhuma batida
    escalados_hoje = (
        AlocacaoDiaria.query
        .filter(AlocacaoDiaria.data == today)
        .with_entities(AlocacaoDiaria.funcionario_id)
        .subquery()
    )
    func_com_batida = (
        Batida.query
        .filter(Batida.data == today)
        .with_entities(Batida.funcionario_id)
        .distinct()
        .subquery()
    )
    ausencias = (
        Funcionario.query
        .filter(
            Funcionario.id.in_(db.session.query(escalados_hoje)),
            Funcionario.id.notin_(db.session.query(func_com_batida)),
            Funcionario.ativo == True,
        )
        .order_by(Funcionario.nome)
        .limit(10)
        .all()
    )

    # Últimas 10 batidas do dia
    ultimas_batidas = (
        Batida.query
        .filter_by(data=today)
        .join(Funcionario)
        .order_by(Batida.hora.desc())
        .limit(10)
        .all()
    )

    ultima_sync = (
        db.session.query(func.max(Funcionario.data_ultima_sincronizacao))
        .scalar()
    )
    last_sync = ultima_sync.strftime('%d/%m %H:%M') if ultima_sync else 'Nunca'

    return render_template(
        'index.html',
        total_funcionarios=total_funcionarios,
        batidas_hoje=batidas_hoje,
        inconsistencias=inconsistencias,
        em_jornada=em_jornada,
        ausencias=ausencias,
        batidas=ultimas_batidas,
        last_sync=last_sync,
    )
