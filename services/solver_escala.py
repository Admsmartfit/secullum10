"""
Solver de Escalas – Auto-sugestão de cobertura e alertas de conflito.
Usados pela Etapa 4 do módulo de Escala Inteligente (prd.md).
"""
import calendar as cal_mod
from datetime import date, timedelta
from models import AlocacaoDiaria, Funcionario, BancoHorasSaldo, Turno, GrupoDepartamento
from extensions import db


def _filtrar_dept(q, dept_str: str):
    """Suporta grupos de departamentos na filtragem."""
    if not dept_str:
        return q
    grupo = GrupoDepartamento.query.filter_by(nome=dept_str).first()
    depts = grupo.departamentos if grupo else [dept_str]
    if len(depts) == 1:
        return q.filter(Funcionario.departamento == depts[0])
    return q.filter(Funcionario.departamento.in_(depts))


def alertas_cobertura(mes_ano: str, dept: str = None, funcao: str = None) -> list[dict]:
    """Retorna lista de dias do mês onde a cobertura para dept/função é zero."""
    try:
        ano, mes = int(mes_ano[:4]), int(mes_ano[5:7])
    except (ValueError, IndexError):
        return []

    _, dias_no_mes = cal_mod.monthrange(ano, mes)
    data_ini = date(ano, mes, 1)
    data_fim = date(ano, mes, dias_no_mes)

    # Funcionários no escopo
    q = Funcionario.query.filter_by(ativo=True)
    q = _filtrar_dept(q, dept)
    if funcao: q = q.filter(Funcionario.funcao == funcao)
    func_ids = {f.id for f in q.all()}
    if not func_ids:
        return []

    # Alocações do mês no escopo
    alocacoes = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.funcionario_id.in_(func_ids),
        AlocacaoDiaria.data >= data_ini,
        AlocacaoDiaria.data <= data_fim,
    ).all()

    dias_com_cobertura = {a.data.day for a in alocacoes}

    descobertos = []
    for d in range(1, dias_no_mes + 1):
        if d not in dias_com_cobertura:
            dt = date(ano, mes, d)
            descobertos.append({
                'data':   dt.isoformat(),
                'dia':    d,
                'dia_semana': dt.weekday(),  # 6 = domingo
                'funcao': funcao or '',
                'dept':   dept or '',
            })
    return descobertos


def violacoes_art386(mes_ano: str, dept: str = None, funcao: str = None) -> list[dict]:
    """Retorna lista de alocações de domingo que violam o Art. 386 (mulheres consecutivos)."""
    try:
        ano, mes = int(mes_ano[:4]), int(mes_ano[5:7])
    except (ValueError, IndexError):
        return []

    _, dias_no_mes = cal_mod.monthrange(ano, mes)
    data_ini = date(ano, mes, 1)
    data_fim = date(ano, mes, dias_no_mes)

    q = Funcionario.query.filter_by(ativo=True, sexo='F')
    q = _filtrar_dept(q, dept)
    if funcao: q = q.filter(Funcionario.funcao == funcao)
    funcs_f = q.all()
    if not funcs_f:
        return []

    violacoes = []
    for func in funcs_f:
        # Alocações em domingos do mês
        alocacoes = AlocacaoDiaria.query.filter(
            AlocacaoDiaria.funcionario_id == func.id,
            AlocacaoDiaria.data >= data_ini,
            AlocacaoDiaria.data <= data_fim,
        ).all()
        domingos = sorted(a.data for a in alocacoes if a.data.weekday() == 6)
        for i, dom in enumerate(domingos):
            if i > 0 and (dom - domingos[i - 1]).days == 7:
                violacoes.append({
                    'func_id':   func.id,
                    'func_nome': func.nome,
                    'data':      dom.isoformat(),
                    'domingo_anterior': domingos[i - 1].isoformat(),
                    'regra': 'Art. 386 CLT – domingos consecutivos',
                })
    return violacoes


def sugerir_substituto(data_ref: date, funcao: str = None, dept: str = None) -> dict | None:
    """
    Encontra o melhor candidato para cobrir 'data_ref':
    1. Funcionários ativos com funcao/dept
    2. Sem alocação no dia
    3. Sem infração CLT bloqueante
    4. Ordenado por menor saldo acumulado no banco de horas (quem mais deve horas)
    Retorna dict com func_id, nome, saldo_banco, turno sugerido, folga_sugerida.
    """
    from services.motor_clt import validar_alocacao

    q = Funcionario.query.filter_by(ativo=True)
    q = _filtrar_dept(q, dept)
    if funcao: q = q.filter(Funcionario.funcao == funcao)
    candidatos = q.order_by(Funcionario.nome).all()

    # Já alocados no dia
    ja_alocados = {
        a.funcionario_id
        for a in AlocacaoDiaria.query.filter_by(data=data_ref).all()
    }

    # Turno padrão do dia (qualquer turno ativo do dept/funcao)
    turno = (
        Turno.query
        .filter(
            Turno.departamento == dept if dept else True,
        )
        .order_by(Turno.id)
        .first()
    ) or Turno.query.order_by(Turno.id).first()

    if not turno:
        return None

    melhores = []
    for func in candidatos:
        if func.id in ja_alocados:
            continue
        infracoes = validar_alocacao(func.id, data_ref, turno)
        bloqueantes = [i for i in infracoes if i.get('severity', 'error') == 'error']
        if bloqueantes:
            continue

        # Saldo banco de horas (último registro)
        saldo_row = (
            BancoHorasSaldo.query
            .filter_by(funcionario_id=func.id)
            .order_by(BancoHorasSaldo.data.desc())
            .first()
        )
        saldo = float(saldo_row.saldo_acumulado) if saldo_row else 0.0
        melhores.append((saldo, func, turno))

    if not melhores:
        return None

    # Ordena por menor saldo (quem mais deve)
    melhores.sort(key=lambda x: x[0])
    saldo, func, turno = melhores[0]

    # Sugere próxima folga compensatória (próxima segunda-feira livre)
    folga_sugerida = None
    for i in range(1, 15):
        d = data_ref + timedelta(days=i)
        if d.weekday() == 0:  # segunda-feira
            tem_aloc = AlocacaoDiaria.query.filter_by(
                funcionario_id=func.id, data=d).first()
            if not tem_aloc:
                folga_sugerida = d.isoformat()
                break

    return {
        'func_id':        func.id,
        'func_nome':      func.nome,
        'funcao':         func.funcao or '',
        'saldo_banco':    round(saldo, 1),
        'turno_id':       turno.id,
        'turno_nome':     turno.nome,
        'turno_inicio':   turno.hora_inicio.strftime('%H:%M'),
        'turno_fim':      turno.hora_fim.strftime('%H:%M'),
        'folga_sugerida': folga_sugerida,
    }
