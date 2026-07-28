"""
Sandbox de testes de conversa WhatsApp — isolado do fluxo real de produção.

Permite simular, para um telefone escolhido pelo administrador, uma conversa
completa com o bot: tanto o envio real (via Evolution API) quanto a
simulação de mensagens "recebidas" (digitadas aqui, sem precisar de um
celular real respondendo) passam pela MESMA lógica de _processar_mensagem
usada em produção (blueprints/whatsapp.py) — menu, check-in, atestado,
opt-in/opt-out etc. — mas usando um Funcionario de teste isolado
(id prefixado com 'SANDBOX-', ativo=False) para não poluir dados reais.

Isolamento: nunca reutiliza um Funcionario real só por coincidência de ID —
mas se o telefone informado já pertencer a um funcionário real, a busca por
celular em _buscar_func_por_celular vai encontrá-lo primeiro (mesmo
comportamento do bot em produção); isso é esperado, não um bug.
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from models import Funcionario, ChatState, WhatsappLog, FilaEnvioWhatsapp

whatsapp_sandbox_bp = Blueprint('whatsapp_sandbox', __name__, url_prefix='/whatsapp/sandbox')

_BOTOES_RAPIDOS = [
    ('menu_escala', '📅 Menu: Minha Escala'),
    ('menu_atraso', '⏰ Menu: Informar Atraso'),
    ('menu_ausencia', '🚨 Menu: Informar Ausência'),
    ('menu_atestado', '🩺 Menu: Enviar Atestado'),
    ('menu_rh', '👤 Menu: Falar com RH'),
    ('opt_sim', '✅ Opt-in: Sim'),
    ('opt_nao', '❌ Opt-in: Não'),
    ('checkin_sim', '✅ Check-in: Sim'),
    ('checkin_nao', '❌ Check-in: Não'),
]


def _somente_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.nivel_acesso != 'administrador':
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated


def _fone_normalizado(celular: str) -> str:
    from services.whatsapp_bot import _fone
    return _fone(celular)


def _get_or_create_funcionario_teste(fone: str) -> 'Funcionario':
    """Funcionario isolado de teste, um por número de telefone (reaproveitado
    entre sessões para manter o histórico da mesma conversa)."""
    fid = f'SANDBOX-{fone}'
    func = Funcionario.query.get(fid)
    if not func:
        func = Funcionario(
            id=fid,
            nome=f'Teste Sandbox ({fone})',
            celular=fone,
            ativo=False,
        )
        db.session.add(func)
        db.session.commit()
    return func


def _montar_timeline(fone: str) -> list:
    """Combina WhatsappLog (histórico já processado) com itens ainda
    'pendente'/'processando' na FilaEnvioWhatsapp (respostas do bot que
    ainda não saíram — o dispatcher real roda a cada ~5s com um delay
    mínimo de humanização, então uma resposta pode demorar alguns segundos
    para sair de verdade, exatamente como em produção)."""
    logs = WhatsappLog.query.filter_by(celular=fone).order_by(WhatsappLog.criado_em.asc()).all()
    fila_pendente = (
        FilaEnvioWhatsapp.query
        .filter(FilaEnvioWhatsapp.celular == fone, FilaEnvioWhatsapp.status.in_(['pendente', 'processando', 'aguardando_optin']))
        .order_by(FilaEnvioWhatsapp.criada_em.asc())
        .all()
    )

    timeline = []
    for log in logs:
        timeline.append({
            'direcao': 'entrada' if log.tipo == 'entrada' else 'saida',
            'texto': log.mensagem,
            'status': log.status,
            'quando': log.criado_em,
            'tipo': log.tipo,
        })
    for item in fila_pendente:
        timeline.append({
            'direcao': 'saida',
            'texto': item.mensagem,
            'status': item.status,
            'quando': item.criada_em,
            'tipo': item.tipo,
        })
    timeline.sort(key=lambda x: x['quando'] or datetime.min)
    return timeline


@whatsapp_sandbox_bp.route('/')
@login_required
@_somente_admin
def index():
    celular = request.args.get('celular', '').strip()
    contexto = {'celular_bruto': celular, 'botoes_rapidos': _BOTOES_RAPIDOS}

    if celular:
        fone = _fone_normalizado(celular)
        func = _get_or_create_funcionario_teste(fone)
        state = ChatState.query.filter_by(funcionario_id=func.id).first()
        contexto.update({
            'fone': fone,
            'func': func,
            'estado_chat': state.estado if state else 'IDLE',
            'timeline': _montar_timeline(fone),
        })

    return render_template('whatsapp/sandbox.html', **contexto)


@whatsapp_sandbox_bp.route('/enviar', methods=['POST'])
@login_required
@_somente_admin
def enviar():
    """Envio real e imediato (como se o sistema/bot iniciasse a conversa)."""
    celular = request.form.get('celular', '').strip()
    mensagem = request.form.get('mensagem', '').strip()
    if not celular or not mensagem:
        flash('Informe o telefone e a mensagem.', 'danger')
        return redirect(url_for('whatsapp_sandbox.index', celular=celular))

    fone = _fone_normalizado(celular)
    func = _get_or_create_funcionario_teste(fone)

    from services.whatsapp_bot import enviar_texto
    ok = enviar_texto(celular=fone, mensagem=mensagem, func_id=func.id, tipo='sandbox_manual', imediato=True)
    flash('Mensagem enviada de verdade via Evolution API!' if ok else 'Falha ao enviar — confira a configuração da Evolution API.',
          'success' if ok else 'danger')
    return redirect(url_for('whatsapp_sandbox.index', celular=celular))


@whatsapp_sandbox_bp.route('/simular', methods=['POST'])
@login_required
@_somente_admin
def simular():
    """Simula uma mensagem 'recebida' do telefone de teste — roda a mesma
    lógica real do bot (_processar_mensagem), que enfileira as respostas
    (despachadas de verdade pelo dispatcher periódico, com o mesmo
    delay/rate-limit humanizado de produção)."""
    celular = request.form.get('celular', '').strip()
    modo = request.form.get('modo', 'texto')
    texto = request.form.get('texto', '').strip()
    btn_id = request.form.get('btn_id', '').strip()

    if not celular:
        flash('Informe o telefone de teste.', 'danger')
        return redirect(url_for('whatsapp_sandbox.index'))

    fone = _fone_normalizado(celular)
    _get_or_create_funcionario_teste(fone)  # garante que o funcionário de teste existe

    data = {'from': f'{fone}@s.whatsapp.net', 'type': 'text'}
    if modo == 'botao' and btn_id:
        data['interactive'] = {'button_reply': {'id': btn_id}}
        data['body'] = data['text'] = f'[botão: {btn_id}]'
    else:
        if not texto:
            flash('Digite o texto a simular.', 'danger')
            return redirect(url_for('whatsapp_sandbox.index', celular=celular))
        data['body'] = data['text'] = texto

    from blueprints.whatsapp import _processar_mensagem
    _processar_mensagem(data)

    return redirect(url_for('whatsapp_sandbox.index', celular=celular))


@whatsapp_sandbox_bp.route('/reset', methods=['POST'])
@login_required
@_somente_admin
def reset():
    """Zera o histórico e o estado da conversa de teste para este telefone
    (não mexe em nenhum dado de funcionário real)."""
    celular = request.form.get('celular', '').strip()
    if not celular:
        return redirect(url_for('whatsapp_sandbox.index'))

    fone = _fone_normalizado(celular)
    fid = f'SANDBOX-{fone}'

    # Escopo estrito por funcionario_id (não por celular): se este número
    # coincidir com um funcionário real, nunca apaga o histórico dele.
    ChatState.query.filter_by(funcionario_id=fid).delete()
    WhatsappLog.query.filter_by(celular=fone, funcionario_id=fid).delete()
    FilaEnvioWhatsapp.query.filter_by(celular=fone, funcionario_id=fid).delete()
    db.session.commit()

    flash('Conversa de teste reiniciada.', 'success')
    return redirect(url_for('whatsapp_sandbox.index', celular=celular))
