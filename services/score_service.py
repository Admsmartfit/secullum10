"""
RF5.5 – Score de Pontualidade.
Fórmula: (dias_com_batida / dias_com_escala) * 100
"""
from datetime import date, timedelta
from extensions import db
from models import AlocacaoDiaria, Batida


def calcular_score(func_id: str, data_inicio: date = None, data_fim: date = None) -> float:
    """Retorna score 0–100. Retorna None se não há escala no período."""
    if not data_fim:
        data_fim = date.today()
    if not data_inicio:
        data_inicio = data_fim - timedelta(days=30)

    # Dias com alocação no período
    alocacoes = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id == func_id,
        AlocacaoDiaria.data >= data_inicio,
        AlocacaoDiaria.data <= data_fim,
    ).all()

    if not alocacoes:
        return None  # sem escala, score indefinido

    dias_com_escala = len(alocacoes)

    # Dias com pelo menos uma batida em dia que tem escala
    datas_escala = {a.data for a in alocacoes}
    batidas = Batida.query.filter(
        Batida.funcionario_id == func_id,
        Batida.data.in_(datas_escala),
    ).with_entities(Batida.data).distinct().all()

    dias_com_batida = len(batidas)
    score = (dias_com_batida / dias_com_escala) * 100
    return round(score, 1)


def calcular_scores_bulk(func_ids: list, data_inicio: date = None, data_fim: date = None) -> dict:
    """Retorna dict {func_id: score} para múltiplos funcionários de uma vez."""
    if not data_fim:
        data_fim = date.today()
    if not data_inicio:
        data_inicio = data_fim - timedelta(days=30)

    # Carregar todas as alocações do período
    alocacoes = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id.in_(func_ids),
        AlocacaoDiaria.data >= data_inicio,
        AlocacaoDiaria.data <= data_fim,
    ).all()

    escala_por_func = {}
    for a in alocacoes:
        escala_por_func.setdefault(a.funcionario_id, set()).add(a.data)

    # Carregar todas as batidas dos dias escalados
    todas_datas = {d for datas in escala_por_func.values() for d in datas}
    if not todas_datas:
        return {fid: None for fid in func_ids}

    batidas = db.session.query(Batida.funcionario_id, Batida.data).filter(
        Batida.funcionario_id.in_(func_ids),
        Batida.data.in_(todas_datas),
    ).distinct().all()

    batidas_por_func = {}
    for fid, data in batidas:
        batidas_por_func.setdefault(fid, set()).add(data)

    scores = {}
    for fid in func_ids:
        datas_esc = escala_por_func.get(fid, set())
        if not datas_esc:
            scores[fid] = None
        else:
            dias_batida = len(batidas_por_func.get(fid, set()) & datas_esc)
            scores[fid] = round((dias_batida / len(datas_esc)) * 100, 1)
    return scores
