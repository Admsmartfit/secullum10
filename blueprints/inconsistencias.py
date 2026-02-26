"""
Módulo de Inconsistências de Batidas.
Detecta e permite corrigir divergências nos registros de ponto.

Tipos de inconsistência detectados:
  - impares      : número ímpar de batidas no dia (entrada sem saída ou vice-versa)
  - duplicata    : duas batidas do mesmo tipo em menos de 5 minutos
  - invertida    : saída registrada antes da entrada
  - longa        : intervalo > 14h entre primeira e última batida do dia
  - curta        : intervalo < 15 min entre duas batidas consecutivas (possível erro)
"""
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from extensions import db
from models import Batida, Funcionario, AlocacaoDiaria

inconsistencias_bp = Blueprint('inconsistencias', __name__, url_prefix='/inconsistencias')

_LIMIAR_DUPLICATA_MIN  = 5    # minutos – batidas mais próximas que isso são "duplicata"
_LIMIAR_CURTA_MIN      = 15   # minutos – intervalo entre entrada/saída considerado curto
_LIMIAR_LONGA_HORAS    = 14   # horas   – turno acima disso é suspeito


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hora_para_min(hora_str):
    """'HH:MM' → minutos desde meia-noite."""
    try:
        h, m = hora_str.strip().split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _analisar_dia(func_obj, data_ref, batidas):
    """
    Recebe lista de Batida do mesmo funcionário/dia.
    Retorna lista de dicts com as inconsistências encontradas.
    """
    problemas = []
    batidas_ord = sorted(batidas, key=lambda b: b.hora)

    # 1. Número ímpar de batidas
    if len(batidas_ord) % 2 != 0:
        problemas.append({
            'tipo':      'impares',
            'icone':     'fa-exclamation-triangle',
            'cor':       'danger',
            'descricao': f'{len(batidas_ord)} batida(s) no dia — número ímpar (entrada ou saída faltando)',
        })

    # 2. Ordem invertida: Saída antes de Entrada, ou sequência Entry→Entry
    tipos_ord = [b.tipo for b in batidas_ord]
    for i in range(len(batidas_ord) - 1):
        b_atual = batidas_ord[i]
        b_prox  = batidas_ord[i + 1]
        min_atual = _hora_para_min(b_atual.hora)
        min_prox  = _hora_para_min(b_prox.hora)
        if min_atual is None or min_prox is None:
            continue

        # Dois do mesmo tipo consecutivos
        if b_atual.tipo == b_prox.tipo:
            diff = min_prox - min_atual
            if diff <= _LIMIAR_DUPLICATA_MIN:
                problemas.append({
                    'tipo':      'duplicata',
                    'icone':     'fa-copy',
                    'cor':       'warning',
                    'descricao': (f'Possível duplicata: {b_atual.tipo} {b_atual.hora} '
                                  f'e {b_prox.tipo} {b_prox.hora} '
                                  f'({diff} min de diferença)'),
                    'batida_ids': [b_atual.id, b_prox.id],
                })
            else:
                problemas.append({
                    'tipo':      'invertida',
                    'icone':     'fa-random',
                    'cor':       'warning',
                    'descricao': (f'Sequência suspeita: {b_atual.tipo} {b_atual.hora} '
                                  f'seguido de {b_prox.tipo} {b_prox.hora}'),
                    'batida_ids': [b_atual.id, b_prox.id],
                })

    # 3. Intervalo entre pares Entrada→Saída
    entradas  = [b for b in batidas_ord if b.tipo == 'Entrada']
    saidas    = [b for b in batidas_ord if b.tipo == 'Saida']
    for ent, sai in zip(entradas, saidas):
        min_e = _hora_para_min(ent.hora)
        min_s = _hora_para_min(sai.hora)
        if min_e is None or min_s is None:
            continue
        diff = min_s - min_e
        if diff < 0:
            problemas.append({
                'tipo':      'invertida',
                'icone':     'fa-random',
                'cor':       'danger',
                'descricao': f'Saída ({sai.hora}) antes da Entrada ({ent.hora})',
                'batida_ids': [ent.id, sai.id],
            })
        elif 0 < diff < _LIMIAR_CURTA_MIN:
            problemas.append({
                'tipo':      'curta',
                'icone':     'fa-compress-arrows-alt',
                'cor':       'warning',
                'descricao': f'Turno muito curto: {ent.hora} → {sai.hora} ({diff} min)',
                'batida_ids': [ent.id, sai.id],
            })

    # 4. Turno total (primeira→última batida) muito longo
    if len(batidas_ord) >= 2:
        min_ini = _hora_para_min(batidas_ord[0].hora)
        min_fim = _hora_para_min(batidas_ord[-1].hora)
        if min_ini is not None and min_fim is not None:
            total_min = min_fim - min_ini
            if total_min > _LIMIAR_LONGA_HORAS * 60:
                h_tot = total_min // 60
                m_tot = total_min % 60
                problemas.append({
                    'tipo':      'longa',
                    'icone':     'fa-clock',
                    'cor':       'info',
                    'descricao': (f'Período total muito longo: {batidas_ord[0].hora} → '
                                  f'{batidas_ord[-1].hora} ({h_tot}h{m_tot:02d}min)'),
                })

    return problemas


def _query_batidas(data_inicio, data_fim, dept=None, func_id=None):
    q = (
        Batida.query
        .join(Funcionario)
        .filter(
            Batida.data >= data_inicio,
            Batida.data <= data_fim,
            Funcionario.ativo == True,
        )
    )
    if func_id:
        q = q.filter(Batida.funcionario_id == func_id)
    elif dept:
        from models import GrupoDepartamento
        grupo = GrupoDepartamento.query.filter_by(nome=dept).first()
        if grupo:
            q = q.filter(Funcionario.departamento.in_(grupo.departamentos))
        else:
            q = q.filter(Funcionario.departamento == dept)
    return q.order_by(Funcionario.nome, Batida.data, Batida.hora).all()


def _departamentos():
    from models import GrupoDepartamento
    grupos = [g.nome for g in GrupoDepartamento.query.order_by(GrupoDepartamento.nome).all()]
    depts = [
        r[0] for r in
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct().order_by(Funcionario.departamento).all()
        if r[0]
    ]
    return grupos + depts


# ── Rotas ─────────────────────────────────────────────────────────────────────

@inconsistencias_bp.route('/')
@login_required
def index():
    hoje = date.today()
    departamentos = _departamentos()
    todos_func    = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    return render_template(
        'inconsistencias/index.html',
        departamentos=departamentos,
        todos_func=todos_func,
        hoje=hoje.strftime('%Y-%m-%d'),
        ontem=(hoje - timedelta(days=1)).strftime('%Y-%m-%d'),
    )


@inconsistencias_bp.route('/analisar')
@login_required
def analisar():
    """AJAX – analisa o período e devolve lista de inconsistências em JSON."""
    try:
        data_inicio = datetime.strptime(request.args['data_inicio'], '%Y-%m-%d').date()
        data_fim    = datetime.strptime(request.args['data_fim'],    '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return jsonify({'error': 'Datas inválidas.'}), 400

    dept    = request.args.get('dept', '').strip() or None
    func_id = request.args.get('func_id', '').strip() or None

    batidas = _query_batidas(data_inicio, data_fim, dept, func_id)

    # Agrupar por (funcionario_id, data)
    grupos: dict[tuple, list] = {}
    for b in batidas:
        key = (b.funcionario_id, b.data)
        grupos.setdefault(key, []).append(b)

    resultados = []
    for (fid, dia), lista in sorted(grupos.items(), key=lambda x: (x[0][1], x[0][0])):
        problemas = _analisar_dia(lista[0].funcionario, dia, lista)
        if not problemas:
            continue
        func = lista[0].funcionario
        resultados.append({
            'funcionario_id':   fid,
            'nome':             func.nome,
            'departamento':     func.departamento or '—',
            'data':             dia.strftime('%Y-%m-%d'),
            'data_fmt':         dia.strftime('%d/%m/%Y'),
            'dia_semana':       ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'][dia.weekday()],
            'problemas':        problemas,
            'batidas': [
                {
                    'id':      b.id,
                    'hora':    b.hora,
                    'tipo':    b.tipo or '',
                    'origem':  b.origem or '',
                    'inconsistente': b.inconsistente,
                }
                for b in sorted(lista, key=lambda b: b.hora)
            ],
        })

    return jsonify({
        'total_dias':    len(grupos),
        'total_erros':   len(resultados),
        'resultados':    resultados,
    })


@inconsistencias_bp.route('/batida/<int:bid>/editar', methods=['POST'])
@login_required
def batida_editar(bid):
    b = Batida.query.get_or_404(bid)
    nova_hora  = request.form.get('hora', '').strip()
    novo_tipo  = request.form.get('tipo', '').strip()
    justif     = request.form.get('justificativa', '').strip()

    if nova_hora:
        # Valida formato HH:MM
        try:
            datetime.strptime(nova_hora, '%H:%M')
        except ValueError:
            return jsonify({'ok': False, 'error': 'Hora inválida (use HH:MM).'}), 400

        # Verifica conflito de unicidade
        conflito = Batida.query.filter(
            Batida.funcionario_id == b.funcionario_id,
            Batida.data           == b.data,
            Batida.hora           == nova_hora,
            Batida.id             != bid,
        ).first()
        if conflito:
            return jsonify({'ok': False, 'error': f'Já existe uma batida às {nova_hora} neste dia.'}), 409

        b.hora     = nova_hora
        try:
            b.data_hora = datetime.combine(b.data, datetime.strptime(nova_hora, '%H:%M').time())
        except Exception:
            pass

    if novo_tipo in ('Entrada', 'Saida'):
        b.tipo = novo_tipo
    if justif:
        b.justificativa = justif
    b.origem           = 'Manual'
    b.inconsistente    = False

    db.session.commit()
    return jsonify({'ok': True, 'hora': b.hora, 'tipo': b.tipo})


@inconsistencias_bp.route('/batida/<int:bid>/excluir', methods=['POST'])
@login_required
def batida_excluir(bid):
    b = Batida.query.get_or_404(bid)
    db.session.delete(b)
    db.session.commit()
    return jsonify({'ok': True})


@inconsistencias_bp.route('/batida/nova', methods=['POST'])
@login_required
def batida_nova():
    func_id = request.form.get('funcionario_id', '').strip()
    data_str = request.form.get('data', '').strip()
    hora    = request.form.get('hora', '').strip()
    tipo    = request.form.get('tipo', 'Entrada').strip()
    justif  = request.form.get('justificativa', '').strip()

    if not func_id or not data_str or not hora:
        return jsonify({'ok': False, 'error': 'Funcionário, data e hora são obrigatórios.'}), 400

    try:
        data_ref = datetime.strptime(data_str, '%Y-%m-%d').date()
        datetime.strptime(hora, '%H:%M')
    except ValueError:
        return jsonify({'ok': False, 'error': 'Data ou hora inválida.'}), 400

    if Batida.query.filter_by(funcionario_id=func_id, data=data_ref, hora=hora).first():
        return jsonify({'ok': False, 'error': f'Já existe uma batida às {hora} neste dia.'}), 409

    b = Batida(
        funcionario_id=func_id,
        data=data_ref,
        hora=hora,
        tipo=tipo,
        origem='Manual',
        justificativa=justif,
        inconsistente=False,
    )
    try:
        b.data_hora = datetime.combine(data_ref, datetime.strptime(hora, '%H:%M').time())
    except Exception:
        pass
    db.session.add(b)
    db.session.commit()
    return jsonify({'ok': True, 'id': b.id, 'hora': b.hora, 'tipo': b.tipo})


# ── Diagnóstico vs Secullum ───────────────────────────────────────────────────

_MARCACOES_ESPECIAIS = {
    'ATESTAD', 'ATESTADO', 'FOLGA', 'FALTA', 'FERIAS', 'NEUTRO',
    'DSRFOL', 'DSRFALTA', 'COMPENSAR',
}


def _extrair_horas_secullum(registro):
    """
    Extrai todas as horas válidas de um registro Secullum (Entrada1..5, Saida1..5).
    Retorna lista de dicts {hora, tipo}.
    """
    horas = []
    for i in range(1, 6):
        for tipo_str, campo in [('Entrada', f'Entrada{i}'), ('Saida', f'Saida{i}')]:
            hora = (registro.get(campo) or '').strip()
            if not hora or hora.upper() in _MARCACOES_ESPECIAIS:
                continue
            if hora in ('00:00', '00:00:00'):
                continue
            # Aceita HH:MM e HH:MM:SS
            partes = hora.split(':')
            if len(partes) < 2:
                continue
            hora_fmt = f'{partes[0]}:{partes[1]}'   # normaliza para HH:MM
            horas.append({'hora': hora_fmt, 'tipo': tipo_str})
    return horas


@inconsistencias_bp.route('/comparar')
@login_required
def comparar():
    """
    AJAX – compara batidas do banco local com o que a API Secullum retorna.
    Parâmetros: data_inicio, data_fim, dept (opcional), func_id (opcional).
    Retorna lista de divergências por (funcionario, data).
    """
    import os
    from secullum_api import SecullumAPI

    try:
        data_inicio = datetime.strptime(request.args['data_inicio'], '%Y-%m-%d').date()
        data_fim    = datetime.strptime(request.args['data_fim'],    '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return jsonify({'error': 'Datas inválidas.'}), 400

    if (data_fim - data_inicio).days > 31:
        return jsonify({'error': 'Período máximo de 31 dias para comparação com a API.'}), 400

    dept    = request.args.get('dept', '').strip() or None
    func_id = request.args.get('func_id', '').strip() or None

    # 1. Buscar do Secullum (sem filtro de hora – dados completos do dia)
    api = SecullumAPI(
        os.getenv('SECULLUM_EMAIL'),
        os.getenv('SECULLUM_PASSWORD'),
        os.getenv('SECULLUM_BANCO'),
    )
    registros_api = api.buscar_batidas(
        data_inicio.strftime('%Y-%m-%d'),
        data_fim.strftime('%Y-%m-%d'),
    )
    if registros_api is None:
        return jsonify({'error': 'Falha ao conectar com a API Secullum.'}), 502

    # Mapear funcionários locais para filtrar dept/func_id
    func_ids_validos = None
    if dept or func_id:
        q = Funcionario.query.filter_by(ativo=True)
        if func_id:
            q = q.filter(Funcionario.id == func_id)
        elif dept:
            from models import GrupoDepartamento
            grupo = GrupoDepartamento.query.filter_by(nome=dept).first()
            if grupo:
                q = q.filter(Funcionario.departamento.in_(grupo.departamentos))
            else:
                q = q.filter(Funcionario.departamento == dept)
        func_ids_validos = {f.id for f in q.all()}

    # 2. Montar mapa Secullum: {(func_id, data): [horas]}
    sec_map: dict[tuple, list] = {}
    from services.sync_service import parse_date
    for reg in registros_api:
        fid = str(reg.get('FuncionarioId'))
        if func_ids_validos is not None and fid not in func_ids_validos:
            continue
        d = parse_date(reg.get('Data'))
        if not d:
            continue
        horas = _extrair_horas_secullum(reg)
        if horas:
            sec_map[(fid, d)] = horas

    # 3. Montar mapa local: {(func_id, data): [batidas]}
    batidas_locais = _query_batidas(data_inicio, data_fim, dept, func_id)
    local_map: dict[tuple, list] = {}
    for b in batidas_locais:
        key = (b.funcionario_id, b.data)
        local_map.setdefault(key, []).append(b)

    # 4. Comparar – só mostra onde há diferença de QUANTIDADE de batidas
    divergencias = []
    todas_chaves = set(sec_map.keys()) | set(local_map.keys())

    func_cache = {f.id: f for f in Funcionario.query.filter_by(ativo=True).all()}

    for (fid, dia) in sorted(todas_chaves, key=lambda x: (x[1], x[0])):
        horas_sec   = sec_map.get((fid, dia), [])
        batidas_loc = local_map.get((fid, dia), [])

        n_sec = len(horas_sec)
        n_loc = len(batidas_loc)

        if n_sec == n_loc:
            continue  # sem divergência de quantidade

        func = func_cache.get(fid)
        nome = func.nome if func else fid
        dept_nome = func.departamento if func else '—'

        # Detalha o que o Secullum tem vs o que temos
        divergencias.append({
            'funcionario_id': fid,
            'nome':           nome,
            'departamento':   dept_nome or '—',
            'data':           dia.strftime('%Y-%m-%d'),
            'data_fmt':       dia.strftime('%d/%m/%Y'),
            'dia_semana':     ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'][dia.weekday()],
            'n_secullum':     n_sec,
            'n_local':        n_loc,
            'faltando':       n_sec - n_loc,  # positivo = temos menos que o Secullum
            'secullum_horas': [h['hora'] + ' (' + h['tipo'] + ')' for h in horas_sec],
            'local_horas':    [b.hora + ' (' + (b.tipo or '?') + ')' for b in sorted(batidas_loc, key=lambda b: b.hora)],
        })

    return jsonify({
        'total_dias_sec':  len(sec_map),
        'total_dias_loc':  len(local_map),
        'divergencias':    divergencias,
    })


@inconsistencias_bp.route('/ressincronizar', methods=['POST'])
@login_required
def ressincronizar():
    """
    Re-sincroniza um dia específico (sem filtro de hora) para um funcionário
    ou para todos do período, usando os dados completos da API Secullum.
    """
    data_str = request.form.get('data', '').strip()

    if not data_str:
        return jsonify({'ok': False, 'error': 'Data obrigatória.'}), 400

    try:
        datetime.strptime(data_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'ok': False, 'error': 'Data inválida.'}), 400

    from services.sync_service import sync_batidas
    ok, msg = sync_batidas(data_str, data_str)   # sem hora_inicio/hora_fim → dia completo
    return jsonify({'ok': ok, 'msg': msg})
