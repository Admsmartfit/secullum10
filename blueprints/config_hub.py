"""
Módulo de Configuração do Sistema.
Gerencia: usuários, líderes de unidade, teste WhatsApp, importação de escalas Secullum.
"""
from datetime import date, timedelta
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from extensions import db
from models import Usuario, Funcionario, UnidadeLider, AlocacaoDiaria, Turno

config_hub_bp = Blueprint('config_hub', __name__, url_prefix='/config')


def _somente_gestor(f):
    """Decorator simples para restringir a gestores."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.nivel_acesso != 'gestor':
            flash('Acesso restrito a gestores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated


# ── Página principal (tabs) ───────────────────────────────────────────────────

@config_hub_bp.route('/')
@login_required
@_somente_gestor
def index():
    usuarios = Usuario.query.order_by(Usuario.nome).all()
    # Departamentos únicos presentes no banco
    depts = (
        db.session.query(Funcionario.departamento)
        .filter(Funcionario.ativo == True, Funcionario.departamento.isnot(None))
        .distinct()
        .order_by(Funcionario.departamento)
        .all()
    )
    departamentos = [d[0] for d in depts if d[0]]
    unidades = {u.departamento: u for u in UnidadeLider.query.all()}
    # Funcionários sem escala nos próximos 7 dias
    hoje = date.today()
    limite = hoje + timedelta(days=7)
    com_escala = (
        db.session.query(AlocacaoDiaria.funcionario_id)
        .filter(AlocacaoDiaria.data.between(hoje, limite))
        .distinct()
        .subquery()
    )
    sem_escala = (
        Funcionario.query
        .filter(Funcionario.ativo == True, Funcionario.id.notin_(db.session.query(com_escala)))
        .order_by(Funcionario.nome)
        .all()
    )
    todos_func = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    import os
    return render_template(
        'config/index.html',
        usuarios=usuarios,
        departamentos=departamentos,
        unidades=unidades,
        sem_escala=sem_escala,
        todos_func=todos_func,
        hoje=hoje.strftime('%Y-%m-%d'),
        fim30=(hoje + timedelta(days=30)).strftime('%Y-%m-%d'),
        megaapi_token=bool(os.getenv('MEGAAPI_TOKEN')),
        megaapi_instance=bool(os.getenv('MEGAAPI_INSTANCE')),
    )


# ── Usuários ──────────────────────────────────────────────────────────────────

@config_hub_bp.route('/usuarios/novo', methods=['POST'])
@login_required
@_somente_gestor
def usuario_novo():
    nome = request.form.get('nome', '').strip()
    email = request.form.get('email', '').strip().lower()
    senha = request.form.get('senha', '').strip()
    nivel = request.form.get('nivel_acesso', 'professor')

    if not nome or not email or not senha:
        flash('Preencha nome, e-mail e senha.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    if Usuario.query.filter_by(email=email).first():
        flash(f'E-mail {email} já cadastrado.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    u = Usuario(nome=nome, email=email, nivel_acesso=nivel, ativo=True)
    u.set_senha(senha)
    db.session.add(u)
    db.session.commit()
    flash(f'Usuário {nome} criado com sucesso.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


@config_hub_bp.route('/usuarios/<int:uid>/editar', methods=['POST'])
@login_required
@_somente_gestor
def usuario_editar(uid):
    u = Usuario.query.get_or_404(uid)
    u.nome = request.form.get('nome', u.nome).strip()
    email = request.form.get('email', u.email).strip().lower()
    nivel = request.form.get('nivel_acesso', u.nivel_acesso)
    ativo = request.form.get('ativo') == '1'
    nova_senha = request.form.get('senha', '').strip()

    # Verificar duplicata de e-mail
    outro = Usuario.query.filter(Usuario.email == email, Usuario.id != uid).first()
    if outro:
        flash('E-mail já utilizado por outro usuário.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')

    u.email = email
    u.nivel_acesso = nivel
    u.ativo = ativo
    if nova_senha:
        u.set_senha(nova_senha)
    db.session.commit()
    flash(f'Usuário {u.nome} atualizado.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


@config_hub_bp.route('/usuarios/<int:uid>/excluir', methods=['POST'])
@login_required
@_somente_gestor
def usuario_excluir(uid):
    u = Usuario.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('Você não pode excluir seu próprio usuário.', 'danger')
        return redirect(url_for('config_hub.index') + '#usuarios')
    db.session.delete(u)
    db.session.commit()
    flash(f'Usuário {u.nome} excluído.', 'success')
    return redirect(url_for('config_hub.index') + '#usuarios')


# ── Unidades / Líderes ────────────────────────────────────────────────────────

@config_hub_bp.route('/unidades/salvar', methods=['POST'])
@login_required
@_somente_gestor
def unidades_salvar():
    """Salva mapeamento departamento → líder + celular."""
    dados = request.get_json(force=True) or {}
    salvos = 0
    for item in dados.get('unidades', []):
        dept = (item.get('departamento') or '').strip()
        if not dept:
            continue
        unidade = UnidadeLider.query.filter_by(departamento=dept).first()
        if not unidade:
            unidade = UnidadeLider(departamento=dept)
            db.session.add(unidade)
        unidade.nome_unidade = (item.get('nome_unidade') or dept).strip()
        unidade.celular_lider = (item.get('celular_lider') or '').strip()
        lider_id = item.get('lider_id')
        unidade.lider_id = int(lider_id) if lider_id else None
        salvos += 1
    db.session.commit()
    return jsonify({'ok': True, 'salvos': salvos})


# ── Teste de WhatsApp ─────────────────────────────────────────────────────────

@config_hub_bp.route('/whatsapp/testar', methods=['POST'])
@login_required
@_somente_gestor
def whatsapp_testar():
    celular = request.form.get('celular', '').strip()
    mensagem = request.form.get('mensagem', 'Teste de comunicação — Secullum Hub ✅').strip()
    if not celular:
        flash('Informe o número de celular para teste.', 'danger')
        return redirect(url_for('config_hub.index') + '#whatsapp')
    from services.whatsapp_bot import enviar_texto
    ok = enviar_texto(celular=celular, mensagem=mensagem, tipo='teste')
    if ok:
        flash(f'Mensagem enviada com sucesso para {celular}!', 'success')
    else:
        flash(
            f'Falha no envio para {celular}. Verifique MEGAAPI_TOKEN e MEGAAPI_INSTANCE no .env. '
            'O log foi registrado em WhatsApp → Logs.',
            'warning',
        )
    return redirect(url_for('config_hub.index') + '#whatsapp')


# ── Importar Escalas do Secullum ──────────────────────────────────────────────

# Secullum DiaSemana: 0=Dom, 1=Seg, 2=Ter, 3=Qua, 4=Qui, 5=Sex, 6=Sab
# Python weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
_SECULLUM_TO_PYTHON = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
_DIAS_NOMES = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def _parsear_horario(horario_raw: dict) -> dict:
    """Converte um item de /Horarios em dict simplificado para o frontend."""
    nome = horario_raw.get('Descricao') or f"Horário {horario_raw.get('Id')}"
    dias_trabalho = []
    hora_inicio = ''
    hora_fim = ''

    for dia in horario_raw.get('Dias', []):
        entrada1 = (dia.get('Entrada1') or '').strip()
        if not entrada1 or entrada1 == '00:00':
            continue  # dia de folga
        dia_sec = dia.get('DiaSemana')
        py_wd = _SECULLUM_TO_PYTHON.get(dia_sec)
        if py_wd is not None:
            dias_trabalho.append(py_wd)

        # Pega início/fim do primeiro dia útil encontrado
        if not hora_inicio:
            hora_inicio = entrada1[:5]
            # Última saída não-vazia do dia
            for i in range(5, 0, -1):
                saida = (dia.get(f'Saida{i}') or '').strip()
                if saida and saida != '00:00':
                    hora_fim = saida[:5]
                    break

    return {
        'horario_id': horario_raw.get('Id'),
        'nome_horario': nome,
        'hora_inicio': hora_inicio,
        'hora_fim': hora_fim,
        'dias_semana': sorted(set(dias_trabalho)),
        'dias_label': ', '.join(_DIAS_NOMES[d] for d in sorted(set(dias_trabalho))),
    }


@config_hub_bp.route('/escalas/preview', methods=['POST'])
@login_required
@_somente_gestor
def escalas_preview():
    """Busca HorarioId de cada funcionário e retorna detalhes do horário para preview."""
    import os
    func_ids_req = [str(f) for f in (request.get_json(force=True) or {}).get('func_ids', [])]
    if not func_ids_req:
        return jsonify({'error': 'Nenhum funcionário selecionado.'}), 400

    from secullum_api import SecullumAPI
    api = SecullumAPI(
        os.getenv('SECULLUM_EMAIL'),
        os.getenv('SECULLUM_PASSWORD'),
        os.getenv('SECULLUM_BANCO'),
    )

    # 1. Busca todos os horários (uma chamada)
    horarios_raw = api.listar_horarios()
    if not horarios_raw:
        return jsonify({'error': 'Não foi possível obter os horários da API Secullum. Verifique as credenciais.'}), 502
    horarios_map = {str(h['Id']): _parsear_horario(h) for h in horarios_raw}

    # 2. Busca funcionários da API para obter HorarioId
    funcs_api = api.listar_funcionarios()
    if not funcs_api:
        return jsonify({'error': 'Não foi possível obter funcionários da API Secullum.'}), 502
    funcs_api_map = {str(f['Id']): f for f in funcs_api}

    # 3. Cruza com funcionários selecionados
    resultados = []
    sem_horario = []
    for fid in func_ids_req[:100]:
        func_db = Funcionario.query.get(fid)
        func_api = funcs_api_map.get(fid)
        nome = func_db.nome if func_db else fid

        if not func_api:
            sem_horario.append(nome)
            continue

        horario_id = str(func_api.get('HorarioId') or '')
        if not horario_id or horario_id not in horarios_map:
            sem_horario.append(nome)
            continue

        h = horarios_map[horario_id]
        if not h['hora_inicio']:
            sem_horario.append(nome)
            continue

        resultados.append({
            'func_id': fid,
            'nome': nome,
            'horario_id': h['horario_id'],
            'nome_horario': h['nome_horario'],
            'hora_inicio': h['hora_inicio'],
            'hora_fim': h['hora_fim'],
            'dias_semana': h['dias_semana'],
            'dias_label': h['dias_label'],
        })

    if not resultados:
        msg = 'Nenhum dos funcionários selecionados possui horário configurado no Secullum.'
        if sem_horario:
            msg += f' Sem horário: {", ".join(sem_horario[:5])}{"..." if len(sem_horario) > 5 else ""}.'
        return jsonify({'error': msg}), 404

    return jsonify({'horarios': resultados, 'sem_horario': sem_horario})


@config_hub_bp.route('/escalas/importar', methods=['POST'])
@login_required
@_somente_gestor
def escalas_importar():
    """Cria Turnos e AlocacaoDiarias a partir do preview confirmado."""
    from datetime import datetime as dt
    dados = request.get_json(force=True) or {}
    horarios = dados.get('horarios', [])
    data_inicio_str = dados.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str = dados.get('data_fim', (date.today() + timedelta(days=30)).strftime('%Y-%m-%d'))

    try:
        d_inicio = dt.strptime(data_inicio_str, '%Y-%m-%d').date()
        d_fim = dt.strptime(data_fim_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Datas inválidas.'}), 400

    turnos_criados = alocacoes_criadas = erros = 0

    for h in horarios:
        nome_turno = (h.get('nome_horario') or 'Sem nome').strip()
        hora_inicio_str = (h.get('hora_inicio') or '').strip()[:5]
        hora_fim_str = (h.get('hora_fim') or '').strip()[:5]
        dias_python = [int(d) for d in (h.get('dias_semana') or [])]
        func_id = str(h.get('func_id', ''))

        if not hora_inicio_str or not hora_fim_str or not func_id or not dias_python:
            erros += 1
            continue

        try:
            t_inicio = dt.strptime(hora_inicio_str, '%H:%M').time()
            t_fim = dt.strptime(hora_fim_str, '%H:%M').time()
        except ValueError:
            erros += 1
            continue

        dias_str = ','.join(str(d) for d in sorted(set(dias_python)))

        turno = Turno.query.filter_by(nome=nome_turno, hora_inicio=t_inicio, hora_fim=t_fim).first()
        if not turno:
            turno = Turno(nome=nome_turno, hora_inicio=t_inicio, hora_fim=t_fim, dias_semana=dias_str)
            db.session.add(turno)
            db.session.flush()
            turnos_criados += 1

        cur = d_inicio
        while cur <= d_fim:
            if cur.weekday() in dias_python:
                existe = AlocacaoDiaria.query.filter_by(funcionario_id=func_id, data=cur).first()
                if not existe:
                    db.session.add(AlocacaoDiaria(funcionario_id=func_id, turno_id=turno.id, data=cur))
                    alocacoes_criadas += 1
            cur += timedelta(days=1)

    db.session.commit()
    return jsonify({
        'ok': True,
        'turnos_criados': turnos_criados,
        'alocacoes_criadas': alocacoes_criadas,
        'erros': erros,
    })
