"""
Motor de cálculo de Banco de Horas – Etapa 3.
Compara horas previstas (escala) x realizadas (batidas Secullum).
"""
from datetime import datetime, timedelta, date
from decimal import Decimal
from extensions import db
from models import AlocacaoDiaria, Batida, BancoHorasSaldo


def _horas_realizadas(func_id: str, data: date) -> float:
    """Calcula horas trabalhadas num dia somando pares entrada/saída.
    Suporta turnos noturnos (ex: 22h-06h do dia seguinte).
    """
    batidas = (
        Batida.query
        .filter_by(funcionario_id=func_id, data=data)
        .order_by(Batida.hora)
        .all()
    )
    entradas = [b for b in batidas if b.tipo == 'Entrada']
    saidas = [b for b in batidas if b.tipo == 'Saida']

    total = 0.0
    for i, entrada in enumerate(entradas):
        if i < len(saidas):
            try:
                h_e = datetime.strptime(entrada.hora, '%H:%M')
                h_s = datetime.strptime(saidas[i].hora, '%H:%M')
                if h_s <= h_e:          # turno noturno: saída no dia seguinte
                    h_s += timedelta(hours=24)
                diff = (h_s - h_e).seconds / 3600
                if 0 < diff <= 16:      # sanidade: no máximo 16h por par
                    total += diff
            except Exception:
                pass
    return round(total, 2)


def calcular_saldo(func_id: str, data_inicio: date, data_fim: date) -> list[dict]:
    """
    Calcula saldo diário e acumulado para um funcionário no período.
    Retorna lista de dicts com: data, previsto, realizado, saldo_dia, saldo_acumulado.
    """
    resultados = []
    saldo_acumulado = Decimal('0')

    # Saldo acumulado anterior ao período
    ultimo = (
        BancoHorasSaldo.query
        .filter(
            BancoHorasSaldo.funcionario_id == func_id,
            BancoHorasSaldo.data < data_inicio,
        )
        .order_by(BancoHorasSaldo.data.desc())
        .first()
    )
    if ultimo:
        saldo_acumulado = ultimo.saldo_acumulado or Decimal('0')

    delta = data_fim - data_inicio
    for i in range(delta.days + 1):
        dia = data_inicio + timedelta(days=i)
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=dia).first()
        previsto = Decimal(str(round(aloc.turno.duracao_horas, 2))) if aloc else Decimal('0')
        realizado = Decimal(str(_horas_realizadas(func_id, dia)))
        saldo_dia = realizado - previsto
        saldo_acumulado += saldo_dia

        resultados.append({
            'data': dia,
            'previsto': float(previsto),
            'realizado': float(realizado),
            'saldo_dia': float(saldo_dia),
            'saldo_acumulado': float(saldo_acumulado),
        })

    return resultados


def salvar_saldos(func_id: str, data_inicio: date, data_fim: date):
    """Persiste os saldos calculados no banco."""
    saldos = calcular_saldo(func_id, data_inicio, data_fim)
    for s in saldos:
        existing = BancoHorasSaldo.query.filter_by(
            funcionario_id=func_id, data=s['data']
        ).first()
        if not existing:
            existing = BancoHorasSaldo(funcionario_id=func_id, data=s['data'])
            db.session.add(existing)
        existing.horas_previstas = s['previsto']
        existing.horas_realizadas = s['realizado']
        existing.saldo_dia = s['saldo_dia']
        existing.saldo_acumulado = s['saldo_acumulado']
    db.session.commit()


def get_config(chave: str, default=None):
    from models import Configuracao
    c = Configuracao.query.filter_by(chave=chave).first()
    return c.valor if c else default


def set_config(chave: str, valor: str):
    from models import Configuracao
    c = Configuracao.query.filter_by(chave=chave).first()
    if not c:
        c = Configuracao(chave=chave)
        db.session.add(c)
    c.valor = valor
    db.session.commit()
