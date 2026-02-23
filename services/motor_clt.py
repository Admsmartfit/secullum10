"""
Motor de validação CLT – Etapa 2.
Valida regras da CLT ao salvar alocações/escalas.
"""
from datetime import datetime, timedelta
from models import AlocacaoDiaria, Batida, Turno


def _combine(data, hora_time):
    return datetime.combine(data, hora_time)


def validar_intrajornada(turno: Turno) -> dict | None:
    """Jornada > 6h exige intervalo mínimo de 1h (CLT art. 71)."""
    duracao = turno.duracao_horas
    if duracao > 6 and duracao < 8:
        # Apenas aviso – não bloqueia
        return None
    return None  # Lógica expandida quando cruzar com batidas reais


def validar_interjornada(func_id: str, data_nova: 'date', turno_novo: Turno) -> dict | None:
    """Entre dois turnos deve haver pelo menos 11h de intervalo (CLT art. 66)."""
    # Verifica alocação no dia anterior
    dia_anterior = data_nova - timedelta(days=1)
    aloc_anterior = (
        AlocacaoDiaria.query
        .filter_by(funcionario_id=func_id, data=dia_anterior)
        .join(Turno)
        .first()
    )
    if aloc_anterior:
        fim_anterior = _combine(dia_anterior, aloc_anterior.turno.hora_fim)
        inicio_novo = _combine(data_nova, turno_novo.hora_inicio)
        # turno noturno: fim pode ser dia seguinte
        if fim_anterior > inicio_novo:
            fim_anterior -= timedelta(days=1)
        intervalo = (inicio_novo - fim_anterior).total_seconds() / 3600
        if intervalo < 11:
            return {
                'error': 'INTERJORNADA',
                'message': f'Intervalo entre turnos de {intervalo:.1f}h é inferior ao mínimo de 11h (CLT art. 66).',
                'horas_encontradas': round(intervalo, 1),
            }

    # Verifica alocação no dia seguinte
    dia_seguinte = data_nova + timedelta(days=1)
    aloc_seguinte = (
        AlocacaoDiaria.query
        .filter_by(funcionario_id=func_id, data=dia_seguinte)
        .join(Turno)
        .first()
    )
    if aloc_seguinte:
        fim_novo = _combine(data_nova, turno_novo.hora_fim)
        inicio_seguinte = _combine(dia_seguinte, aloc_seguinte.turno.hora_inicio)
        if fim_novo > inicio_seguinte:
            inicio_seguinte += timedelta(days=1)
        intervalo = (inicio_seguinte - fim_novo).total_seconds() / 3600
        if intervalo < 11:
            return {
                'error': 'INTERJORNADA',
                'message': f'Intervalo entre turnos de {intervalo:.1f}h é inferior ao mínimo de 11h (CLT art. 66).',
                'horas_encontradas': round(intervalo, 1),
            }

    return None


def validar_carga_semanal(func_id: str, data_nova: 'date', turno_novo: Turno) -> dict | None:
    """Máximo 44h semanais (CLT art. 58)."""
    # Calcular início da semana (segunda-feira)
    dia_semana = data_nova.weekday()
    inicio_semana = data_nova - timedelta(days=dia_semana)
    fim_semana = inicio_semana + timedelta(days=6)

    alocacoes = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id == func_id,
        AlocacaoDiaria.data >= inicio_semana,
        AlocacaoDiaria.data <= fim_semana,
        AlocacaoDiaria.data != data_nova,
    ).all()

    total_horas = sum(a.turno.duracao_horas for a in alocacoes) + turno_novo.duracao_horas
    if total_horas > 44:
        return {
            'error': 'CARGA_SEMANAL',
            'message': f'Carga semanal de {total_horas:.1f}h excede o limite de 44h (CLT art. 58).',
            'horas_encontradas': round(total_horas, 1),
        }
    return None


def validar_alocacao(func_id: str, data: 'date', turno: Turno) -> list[dict]:
    """
    Executa todas as validações CLT para uma nova alocação.
    Retorna lista de infrações (vazia = OK).
    """
    from datetime import date as date_type  # import local para type check
    infracoes = []

    erro = validar_interjornada(func_id, data, turno)
    if erro:
        infracoes.append(erro)

    erro = validar_carga_semanal(func_id, data, turno)
    if erro:
        infracoes.append(erro)

    return infracoes
