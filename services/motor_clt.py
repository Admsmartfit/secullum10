"""
Motor de validação CLT – Etapa 2.
Valida regras da CLT ao salvar alocações/escalas.
"""
from datetime import datetime, timedelta
from models import AlocacaoDiaria, Turno, Funcionario


def _combine(data, hora_time):
    return datetime.combine(data, hora_time)


def validar_intrajornada(turno: Turno, data_ref: 'date') -> dict | None:
    """Jornada > 6h exige intervalo mínimo de 1h (CLT art. 71)."""
    duracao = turno.duracao_horas_no_dia(data_ref)
    _, _, intervalo = turno.get_horario_dia(data_ref.weekday())
    
    if duracao > 6 and intervalo < 60:
        return {
            'error': 'INTRAJORNADA',
            'message': f'Jornada superior a 6h ({duracao:.1f}h) exige intervalo de pelo menos 60 min (Atual: {intervalo} min).',
        }
    elif duracao > 4 and duracao <= 6 and intervalo < 15:
         return {
            'error': 'INTRAJORNADA',
            'message': f'Jornada entre 4h e 6h exige intervalo de pelo menos 15 min (Atual: {intervalo} min).',
        }
    return None


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
        h_ini_ant, h_fim_ant, _ = aloc_anterior.turno.get_horario_dia(dia_anterior.weekday())
        fim_anterior = _combine(dia_anterior, h_fim_ant)
        
        h_ini_novo, _, _ = turno_novo.get_horario_dia(data_nova.weekday())
        inicio_novo = _combine(data_nova, h_ini_novo)
        
        # turno noturno: fim pode ser dia seguinte
        if h_fim_ant < h_ini_ant:
            fim_anterior += timedelta(days=1)
            
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
        _, h_fim_novo, _ = turno_novo.get_horario_dia(data_nova.weekday())
        fim_novo = _combine(data_nova, h_fim_novo)
        
        h_ini_seg, _, _ = aloc_seguinte.turno.get_horario_dia(dia_seguinte.weekday())
        inicio_seguinte = _combine(dia_seguinte, h_ini_seg)
        
        if h_fim_novo < h_ini_novo: # h_ini_novo precisa ser pego acima
            fim_novo += timedelta(days=1)
            
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

    total_horas = sum(a.turno.duracao_horas_no_dia(a.data) for a in alocacoes) + turno_novo.duracao_horas_no_dia(data_nova)
    if total_horas > 44:
        return {
            'error': 'CARGA_SEMANAL',
            'message': f'Carga semanal de {total_horas:.1f}h excede o limite de 44h (CLT art. 58).',
            'horas_encontradas': round(total_horas, 1),
        }
    return None


def validar_dsr(func_id: str, data_nova: 'date') -> dict | None:
    """Pelo menos 1 folga em cada janela de 7 dias corridos (CLT art. 67 – DSR)."""
    inicio = data_nova - timedelta(days=6)
    count = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id == func_id,
        AlocacaoDiaria.data >= inicio,
        AlocacaoDiaria.data <= data_nova,
    ).count()
    if count >= 6:
        return {
            'error': 'DSR',
            'message': 'Funcionário escalado 7 dias consecutivos sem folga (CLT art. 67 – DSR).',
        }
    return None


def validar_domingos_consecutivos(func_id: str, data_nova: 'date') -> dict | None:
    """Art. 386 CLT – mulheres não podem trabalhar domingos consecutivos sem revezamento.
    Aplica-se apenas a funcionárias com sexo='F'.
    Retorna aviso (severity='warning') se o dia anterior de domingo estiver alocado.
    """
    if data_nova.weekday() != 6:   # não é domingo
        return None
    func = Funcionario.query.get(func_id)
    if not func or func.sexo != 'F':
        return None
    # Domingo anterior (7 dias atrás)
    domingo_anterior = data_nova - timedelta(days=7)
    trabalhou = AlocacaoDiaria.query.filter_by(
        funcionario_id=func_id, data=domingo_anterior
    ).first()
    if trabalhou:
        return {
            'error': 'DOMINGO_CONSECUTIVO',
            'message': (
                f'Art. 386 CLT: {func.nome} trabalhou no domingo anterior '
                f'({domingo_anterior.strftime("%d/%m")}). '
                'O revezamento quinzenal é obrigatório para mulheres.'
            ),
            'severity': 'warning',
        }
    return None


def validar_alocacao(func_id: str, data: 'date', turno: Turno) -> list[dict]:
    """
    Executa todas as validações CLT para uma nova alocação.
    Retorna lista de infrações (vazia = OK).
    """
    infracoes = []

    erro = validar_intrajornada(turno, data)
    if erro:
        infracoes.append(erro)

    erro = validar_interjornada(func_id, data, turno)
    if erro:
        infracoes.append(erro)

    erro = validar_carga_semanal(func_id, data, turno)
    if erro:
        infracoes.append(erro)

    erro = validar_dsr(func_id, data)
    if erro:
        infracoes.append(erro)

    erro = validar_domingos_consecutivos(func_id, data)
    if erro:
        infracoes.append(erro)

    return infracoes
