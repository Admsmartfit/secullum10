import hmac, hashlib, os, json
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from extensions import db
from models import WhatsappLog, Funcionario, AlocacaoDiaria, UnidadeLider, Batida, NotificacaoFila, ChatState

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

def _megaapi_secret():
    from services.config_service import get_setting
    return get_setting('megaapi_secret', 'MEGAAPI_SECRET', '')

def _gestor_celular():
    from services.config_service import get_gestor_celular
    return get_gestor_celular()


def _bot_msg(chave: str, vars: dict = None) -> str:
    """Lê texto do robô da tabela Configuracao, interpola variáveis {{key}}."""
    from models import Configuracao
    from blueprints.notificacoes import BOT_MSG_DEFAULTS
    row = Configuracao.query.filter_by(chave=chave).first()
    texto = row.valor if (row and row.valor) else BOT_MSG_DEFAULTS.get(chave, '')
    if vars:
        for k, v in vars.items():
            texto = texto.replace(f'{{{{{k}}}}}', str(v))
    return texto


def _celular_lider(func: 'Funcionario') -> str:
    """Retorna o celular do líder da unidade do funcionário.
    Fallback: GESTOR_CELULAR global do .env."""
    if func and func.departamento:
        ul = UnidadeLider.query.filter_by(departamento=func.departamento).first()
        if ul and ul.celular_lider:
            return ul.celular_lider
    return _gestor_celular()


def _validar_hmac(payload_bytes: bytes, signature: str) -> bool:
    """Valida assinatura HMAC-SHA256 do webhook Mega-API (RF4.1)."""
    secret = _megaapi_secret()
    if not secret:
        return True  # em dev, aceitar sem validação
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


# ── Webhook ───────────────────────────────────────────────────────────────────

@whatsapp_bp.route('/webhook', methods=['POST'])
def webhook():
    """RF4.1 – recebe mensagens da Mega-API e enfileira processamento."""
    payload_bytes = request.get_data()
    signature = request.headers.get('X-Mega-Signature', '')

    if not _validar_hmac(payload_bytes, signature):
        return jsonify({'error': 'invalid signature'}), 401

    data = request.get_json(force=True, silent=True) or {}

    # Enfileirar via Celery para processar < 2s (RF4.1)
    try:
        from tasks import processar_webhook_whatsapp
        processar_webhook_whatsapp.delay(data)
    except Exception:
        # fallback síncrono em dev
        _processar_mensagem(data)

    return jsonify({'ok': True}), 200


def _get_or_create_state(func_id: str) -> 'ChatState':
    """Retorna ou cria o ChatState do funcionário."""
    state = ChatState.query.filter_by(funcionario_id=func_id).first()
    if not state:
        state = ChatState(funcionario_id=func_id, estado='IDLE')
        db.session.add(state)
    return state


def _set_state(func_id: str, estado: str, contexto: dict = None):
    state = _get_or_create_state(func_id)
    state.estado = estado
    state.contexto = json.dumps(contexto or {})
    state.atualizado_em = datetime.utcnow()
    db.session.commit()


def _enviar_menu_principal(func: 'Funcionario'):
    """Envia o Menu Principal como List Message."""
    from services.whatsapp_bot import enviar_menu_lista
    nome = func.nome.split()[0]
    enviar_menu_lista(
        celular=func.celular,
        texto=f'Olá, *{nome}*! 👋 Como posso ajudar?',
        titulo_botao='Ver opções',
        secoes=[{
            'title': 'Autoatendimento',
            'rows': [
                {'id': 'menu_escala',   'title': '📅 Minha Escala',    'description': 'Ver turnos dos próximos 7 dias'},
                {'id': 'menu_atraso',   'title': '⏰ Informar Atraso', 'description': 'Avisar que vai atrasar'},
                {'id': 'menu_ausencia', 'title': '🚨 Informar Ausência','description': 'Confirmar falta hoje'},
                {'id': 'menu_atestado', 'title': '🩺 Enviar Atestado', 'description': 'Foto ou PDF do atestado'},
                {'id': 'menu_rh',       'title': '👤 Falar com RH',    'description': 'Encaminhar para atendimento humano'},
            ],
        }],
        func_id=func.id,
        tipo='menu_principal',
    )


def _processar_mensagem(data: dict):
    """Processa mensagem inbound da Mega-API.

    Cenários suportados:
    1. Interactive reply (button_reply / list_reply) — clique num botão ou lista
    2. Mídia (image/document) — atestado médico quando estado == AGUARDANDO_ATESTADO
    3. Áudio — transcreve via Whisper e reprocessa como texto
    4. Texto:
       a. Saudações ("oi", "menu"…) → Menu Principal
       b. Estado AGUARDANDO_MINUTOS_ATRASO → número de minutos
       c. SIM/NÃO (botão de check-in prévio)
       d. Justificativa automática de batida inconsistente
       e. Texto livre → transbordo ao gestor
    """
    from services.whatsapp_bot import enviar_texto, enviar_botoes, baixar_midia

    celular = data.get('from', '').replace('@s.whatsapp.net', '')
    tipo_msg = data.get('type', 'text')  # text | audio | ptt | image | document | interactive

    # ── Identificar funcionário ───────────────────────────────────────────────
    digits = ''.join(c for c in celular if c.isdigit())[-11:]
    func = Funcionario.query.filter(
        Funcionario.celular.like(f'%{digits[-8:]}%')
    ).first()

    # ── Log de entrada ────────────────────────────────────────────────────────
    log_mensagem = data.get('body') or data.get('text') or tipo_msg
    log = WhatsappLog(
        funcionario_id=func.id if func else None,
        tipo='entrada',
        mensagem=str(log_mensagem)[:500],
        celular=celular,
        status='recebido',
        criado_em=datetime.utcnow(),
    )
    db.session.add(log)
    db.session.commit()

    if not func or not celular:
        return

    state = _get_or_create_state(func.id)
    estado_atual = state.estado or 'IDLE'
    ctx = json.loads(state.contexto or '{}')

    # ══════════════════════════════════════════════════════════════════════════
    # 1. Interactive reply (button_reply ou list_reply)
    # ══════════════════════════════════════════════════════════════════════════
    interactive = data.get('interactive') or data.get('buttonResponse') or {}
    btn_id = (
        (interactive.get('button_reply') or {}).get('id')
        or (interactive.get('list_reply') or {}).get('id')
        or interactive.get('selectedButtonId')
        or interactive.get('selectedRowId')
        or ''
    )

    if btn_id:
        _processar_interactive(func, btn_id, estado_atual, ctx)
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 2. Mídia (imagem / documento) → Atestado
    # ══════════════════════════════════════════════════════════════════════════
    if tipo_msg in ('image', 'document'):
        if estado_atual == 'AGUARDANDO_ATESTADO':
            _processar_atestado(func, data)
        else:
            # Mídia fora de contexto → transbordo
            lider_cel = _celular_lider(func)
            if lider_cel:
                enviar_texto(
                    celular=lider_cel,
                    mensagem=f'📎 *{func.nome}* enviou uma mídia ({tipo_msg}). Verifique pelo painel.',
                    func_id=func.id,
                    tipo='transbordo_midia',
                )
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 3. Áudio → transcreve
    # ══════════════════════════════════════════════════════════════════════════
    if tipo_msg in ('audio', 'ptt'):
        texto = _transcrever_audio(data)
        if not texto:
            return
    else:
        texto = (data.get('body') or data.get('text') or '').strip()

    if not texto:
        return

    resposta_upper = texto.upper().strip()

    # ══════════════════════════════════════════════════════════════════════════
    # 4a. Estado AGUARDANDO_MINUTOS_ATRASO → recebe número
    # ══════════════════════════════════════════════════════════════════════════
    if estado_atual == 'AGUARDANDO_MINUTOS_ATRASO':
        try:
            minutos = int(''.join(c for c in texto if c.isdigit()))
        except (ValueError, TypeError):
            minutos = None
        if minutos and 1 <= minutos <= 480:
            lider_cel = _celular_lider(func)
            if lider_cel:
                enviar_texto(
                    celular=lider_cel,
                    mensagem=f'⏰ *{func.nome}* avisou que vai atrasar *{minutos} min* hoje.',
                    func_id=func.id,
                    tipo='aviso_atraso',
                )
            enviar_texto(
                celular=func.celular,
                mensagem=f'Certo, {func.nome.split()[0]}! Aviso de *{minutos} min* de atraso enviado ao seu gestor. ✅',
                func_id=func.id,
                tipo='aviso_atraso_confirmado',
            )
            _set_state(func.id, 'IDLE')
        else:
            enviar_texto(
                celular=func.celular,
                mensagem='Por favor, informe apenas o número de minutos (ex: "30").',
                func_id=func.id,
                tipo='bot_instrucao',
            )
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 4b. Palavras-chave personalizadas (banco de dados) — verificadas antes do menu
    # ══════════════════════════════════════════════════════════════════════════
    from models import BotKeywordRule
    from services.whatsapp_bot import enviar_msg as _enviar_msg
    for kw_rule in BotKeywordRule.query.filter_by(ativo=True).all():
        if resposta_upper == kw_rule.keyword.upper().strip():
            resp_kw = kw_rule.resposta.replace('{{nome}}', func.nome.split()[0])
            _enviar_msg(
                celular=func.celular, texto=resp_kw,
                tipo_msg=getattr(kw_rule, 'tipo_msg', None) or 'texto',
                interativo_json=getattr(kw_rule, 'interativo_json', None),
                func_id=func.id, tipo='keyword_rule',
            )
            if not kw_rule.apenas_funcionario:
                lider_cel = _celular_lider(func)
                if lider_cel:
                    enviar_texto(celular=lider_cel,
                                 mensagem=f'[{kw_rule.keyword}] {func.nome}: {resp_kw}',
                                 func_id=func.id, tipo='keyword_rule')
            _set_state(func.id, 'IDLE')
            return

    # ══════════════════════════════════════════════════════════════════════════
    # 4c. Saudações → Menu Principal
    # ══════════════════════════════════════════════════════════════════════════
    _SAUDACOES = {'OI', 'OLÁ', 'OLA', 'MENU', 'OPCOES', 'OPÇÕES', 'AJUDA', 'HELP', 'START', 'INICIO', 'INÍCIO', 'OIE', 'EI'}
    if resposta_upper in _SAUDACOES or resposta_upper.startswith('MENU'):
        _set_state(func.id, 'IDLE')
        _enviar_menu_principal(func)
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 4c. SIM/NÃO (check-in prévio via texto — fallback de botão)
    # ══════════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════════
    # 4d. SIM/NÃO (check-in prévio via texto — fallback de botão)
    # ══════════════════════════════════════════════════════════════════════════
    if resposta_upper in ('SIM', 'S', '1', 'BTN_SIM', 'CHECKIN_SIM'):
        hoje = date.today()
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func.id, data=hoje).first()
        if aloc and not aloc.pre_checkin:
            aloc.pre_checkin = True
            db.session.commit()
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_sim_func', {'nome': func.nome.split()[0]}),
            func_id=func.id,
            tipo='checkin_confirmado',
        )
        _set_state(func.id, 'IDLE')
        return

    if resposta_upper in ('NÃO', 'NAO', 'N', '0', 'BTN_NAO', 'CHECKIN_NAO'):
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=_bot_msg('bot_msg_nao_lider', {'nome': func.nome}),
                func_id=func.id,
                tipo='ausencia_confirmada',
            )
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_nao_func', {'nome': func.nome.split()[0]}),
            func_id=func.id,
            tipo='ausencia_confirmada',
        )
        _set_state(func.id, 'IDLE')
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 4d. Justificativa automática de batida inconsistente
    # ══════════════════════════════════════════════════════════════════════════
    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    ultima_inc = (
        Batida.query
        .filter(Batida.funcionario_id == func.id)
        .filter(Batida.data.in_([hoje, ontem]))
        .filter(Batida.inconsistente == True)
        .order_by(Batida.data.desc(), Batida.hora.desc())
        .first()
    )
    if ultima_inc:
        prefixo = f"[WhatsApp {datetime.now().strftime('%d/%m %H:%M')}] "
        nova_just = f"{prefixo}{texto}"
        if ultima_inc.justificativa:
            ultima_inc.justificativa = f"{ultima_inc.justificativa}\n{nova_just}"
        else:
            ultima_inc.justificativa = nova_just
        ultima_inc.justificada_via = 'Bot'
        db.session.commit()
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_justificativa_func', {
                'nome': func.nome.split()[0],
                'data': ultima_inc.data.strftime('%d/%m'),
            }),
            func_id=func.id,
            tipo='justificativa_automatica',
        )
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=_bot_msg('bot_msg_justificativa_lider', {
                    'nome': func.nome,
                    'mensagem': texto,
                }),
                func_id=func.id,
                tipo='notificacao_gestor_justificativa',
            )
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 4e. Texto livre → transbordo ao gestor
    # ══════════════════════════════════════════════════════════════════════════
    lider_cel = _celular_lider(func)
    if lider_cel:
        enviar_texto(
            celular=lider_cel,
            mensagem=_bot_msg('bot_msg_transbordo_lider', {
                'nome': func.nome,
                'mensagem': texto,
            }),
            func_id=func.id,
            tipo='notificacao_gestor',
        )


def _processar_interactive(func: 'Funcionario', btn_id: str, _estado: str, _ctx: dict):
    """Processa clique em botão ou seleção de lista."""
    from services.whatsapp_bot import enviar_texto, enviar_botoes
    from services.notification_processor import _montar_escala

    nome = func.nome.split()[0]

    if btn_id == 'menu_escala':
        escala = _montar_escala(func, date.today())
        enviar_texto(
            celular=func.celular,
            mensagem=f'📅 *Sua escala — próximos 7 dias:*\n\n{escala}',
            func_id=func.id,
            tipo='escala_solicitada',
        )
        _set_state(func.id, 'IDLE')

    elif btn_id == 'menu_atraso':
        enviar_texto(
            celular=func.celular,
            mensagem='⏰ Quantos minutos você vai atrasar? (Ex: *30*)',
            func_id=func.id,
            tipo='bot_instrucao',
        )
        _set_state(func.id, 'AGUARDANDO_MINUTOS_ATRASO')

    elif btn_id == 'menu_ausencia':
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=_bot_msg('bot_msg_nao_lider', {'nome': func.nome}),
                func_id=func.id,
                tipo='ausencia_confirmada',
            )
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_nao_func', {'nome': nome}),
            func_id=func.id,
            tipo='ausencia_confirmada',
        )
        _set_state(func.id, 'IDLE')

    elif btn_id == 'menu_atestado':
        enviar_texto(
            celular=func.celular,
            mensagem='🩺 Por favor, envie agora a *foto ou PDF* do seu atestado médico.',
            func_id=func.id,
            tipo='bot_instrucao',
        )
        _set_state(func.id, 'AGUARDANDO_ATESTADO')

    elif btn_id == 'menu_rh':
        enviar_texto(
            celular=func.celular,
            mensagem='👤 Transferindo para atendimento humano... O RH/Gestor entrará em contato em breve.',
            func_id=func.id,
            tipo='transbordo_rh',
        )
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=f'💬 *{func.nome}* solicitou falar com o RH/Gestor via WhatsApp.',
                func_id=func.id,
                tipo='transbordo_rh',
            )
        _set_state(func.id, 'IDLE')

    # Botões de check-in prévio (PRE_CHECKIN)
    elif btn_id in ('checkin_sim', 'btn_sim'):
        hoje = date.today()
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func.id, data=hoje).first()
        if aloc and not aloc.pre_checkin:
            aloc.pre_checkin = True
            db.session.commit()
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_sim_func', {'nome': nome}),
            func_id=func.id,
            tipo='checkin_confirmado',
        )
        _set_state(func.id, 'IDLE')

    elif btn_id in ('checkin_nao', 'btn_nao'):
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=_bot_msg('bot_msg_nao_lider', {'nome': func.nome}),
                func_id=func.id,
                tipo='ausencia_confirmada',
            )
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_nao_func', {'nome': nome}),
            func_id=func.id,
            tipo='ausencia_confirmada',
        )
        _set_state(func.id, 'IDLE')

    else:
        # ID desconhecido → reapresenta menu
        _enviar_menu_principal(func)


def _processar_atestado(func: 'Funcionario', data: dict):
    """Baixa e salva o atestado enviado pelo funcionário."""
    from services.whatsapp_bot import enviar_texto, baixar_midia

    media_url = (
        data.get('mediaUrl') or data.get('url')
        or (data.get('media') or {}).get('url', '')
    )
    tipo_msg = data.get('type', 'image')

    if not media_url:
        enviar_texto(
            celular=func.celular,
            mensagem='Não consegui receber o arquivo. Tente novamente ou envie pelo painel.',
            func_id=func.id,
            tipo='bot_erro',
        )
        return

    conteudo = baixar_midia(media_url)
    if not conteudo:
        enviar_texto(
            celular=func.celular,
            mensagem='Falha ao baixar o arquivo. Por favor, tente novamente.',
            func_id=func.id,
            tipo='bot_erro',
        )
        return

    # Salva no disco e registra em ProntuarioDoc
    from models import ProntuarioDoc
    import uuid, pathlib
    from flask import current_app

    ext = 'pdf' if tipo_msg == 'document' else 'jpg'
    nome_arquivo = f'atestado_{func.id}_{date.today().isoformat()}_{uuid.uuid4().hex[:6]}.{ext}'
    pasta = pathlib.Path(current_app.root_path) / 'storage' / 'prontuario' / func.id
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome_arquivo
    caminho.write_bytes(conteudo)

    doc = ProntuarioDoc(
        funcionario_id=func.id,
        tipo='Atestado Médico',
        nome_arquivo=nome_arquivo,
        arquivo_path=str(caminho),
    )
    db.session.add(doc)
    _set_state(func.id, 'IDLE')
    db.session.commit()

    # Confirma ao funcionário
    enviar_texto(
        celular=func.celular,
        mensagem=_bot_msg('bot_msg_atestado_func', {
            'nome': func.nome.split()[0],
            'data': date.today().strftime('%d/%m/%Y'),
        }),
        func_id=func.id,
        tipo='atestado_recebido',
    )

    # Notifica líder
    lider_cel = _celular_lider(func)
    if lider_cel:
        enviar_texto(
            celular=lider_cel,
            mensagem=_bot_msg('bot_msg_atestado_lider', {
                'nome': func.nome,
                'data': date.today().strftime('%d/%m/%Y'),
            }),
            func_id=func.id,
            tipo='atestado_lider',
        )


def _transcrever_audio(data: dict) -> str:
    """RF4.3 – Transcreve áudio via OpenAI Whisper API.
    Retorna texto transcrito ou '' se falhar ou não configurado.
    """
    import requests as req_lib
    import os
    openai_key = os.getenv('OPENAI_API_KEY', '')
    if not openai_key:
        return ''
    try:
        audio_url = data.get('mediaUrl') or data.get('url') or ''
        if not audio_url:
            return ''
        audio_data = req_lib.get(audio_url, timeout=15).content
        from io import BytesIO
        files = {'file': ('audio.ogg', BytesIO(audio_data), 'audio/ogg')}
        headers = {'Authorization': f'Bearer {openai_key}'}
        resp = req_lib.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers=headers,
            files=files,
            data={'model': 'whisper-1', 'language': 'pt'},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get('text', '')
    except Exception:
        pass
    return ''


# ── Painel de Logs ────────────────────────────────────────────────────────────

@whatsapp_bp.route('/logs')
@login_required
def logs():
    logs_lista = (
        WhatsappLog.query
        .order_by(WhatsappLog.criado_em.desc())
        .limit(200)
        .all()
    )
    fila = (
        NotificacaoFila.query
        .filter(NotificacaoFila.status == 'pendente')
        .order_by(NotificacaoFila.criada_em.desc())
        .all()
    )
    funcionarios = (
        Funcionario.query
        .filter(Funcionario.celular.isnot(None), Funcionario.ativo == True)
        .order_by(Funcionario.nome)
        .all()
    )
    return render_template('whatsapp/logs.html', 
                           logs=logs_lista, 
                           fila=fila,
                           funcionarios=funcionarios)


@whatsapp_bp.route('/enviar', methods=['POST'])
@login_required
def enviar():
    """Envio manual de mensagem para um funcionário."""
    func_id = request.form.get('funcionario_id')
    mensagem = request.form.get('mensagem', '').strip()
    func = Funcionario.query.get_or_404(func_id)

    if not func.celular:
        flash('Funcionário sem celular cadastrado.', 'danger')
        return redirect(url_for('whatsapp.logs'))

    from services.whatsapp_bot import enviar_texto
    ok = enviar_texto(celular=func.celular, mensagem=mensagem, func_id=func_id, tipo='manual')
    flash('Mensagem enviada!' if ok else 'Falha ao enviar (verifique MEGAAPI_TOKEN).', 'success' if ok else 'danger')
    return redirect(url_for('whatsapp.logs'))


@whatsapp_bp.route('/teste', methods=['GET', 'POST'])
@login_required
def teste():
    """Módulo de testes para envio dos diversos tipos de mensagem do sistema."""
    if request.method == 'POST':
        celular = request.form.get('celular', '').strip()
        tipo_msg = request.form.get('tipo_msg', '').strip()
        
        if not celular:
            flash('Informe o celular de destino.', 'danger')
            return redirect(url_for('whatsapp.teste'))
            
        from services.whatsapp_bot import enviar_texto, enviar_documento
        
        ok = False
        if tipo_msg == 'texto_livre':
            texto = request.form.get('mensagem_livre', 'Teste de mensagem livre do sistema.')
            ok = enviar_texto(celular, texto, tipo='teste_manual')
            
        elif tipo_msg == 'documento':
            # Dummy minimalist PDF content
            pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Count 1\n/Kids [ 3 0 R ]\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [ 0 0 612 792 ]\n/Resources <<\n/Font <<\n/F1 4 0 R\n>>\n>>\n/Contents 5 0 R\n>>\nendobj\n4 0 obj\n<<\n/Type /Font\n/Subtype /Type1\n/BaseFont /Helvetica\n>>\nendobj\n5 0 obj\n<<\n/Length 55\n>>\nstream\nBT\n/F1 24 Tf\n100 700 Td\n(Documento de Teste do Sistema) Tj\nET\nendstream\nendobj\nxref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000219 00000 n \n0000000307 00000 n \ntrailer\n<<\n/Size 6\n/Root 1 0 R\n>>\nstartxref\n413\n%%EOF\n"
            ok = enviar_documento(celular, pdf_bytes, 'documento_teste.pdf', caption='Segue o documento de teste.', tipo='teste_pdf')
            
        elif tipo_msg == 'checkin_confirmado':
            texto = "Perfeito, João! Presença confirmada. Bom turno!"
            ok = enviar_texto(celular, texto, tipo='teste_checkin')
            
        elif tipo_msg == 'ausencia_confirmada':
            texto = "Entendido! Sua ausência foi registrada. Qualquer problema, entre em contato com o RH."
            ok = enviar_texto(celular, texto, tipo='teste_ausencia')
            
        elif tipo_msg == 'justificativa_automatica':
            data_hoje = date.today().strftime("%d/%m")
            texto = f"✅ Recebido! Sua justificativa para o dia {data_hoje} foi registrada no espelho de ponto."
            ok = enviar_texto(celular, texto, tipo='teste_justificativa')
            
        elif tipo_msg == 'notificacao_gestor_justificativa':
            texto = "📝 *Maria* enviou uma justificativa via WhatsApp:\n\"Meu ônibus quebrou na avenida principal.\""
            ok = enviar_texto(celular, texto, tipo='teste_notificacao_gestor')
            
        elif tipo_msg == 'boas_vindas':
            texto = "🎉 *Bem-vindo(a) à nossa plataforma!*\nSeu cadastro foi realizado com sucesso. Agora você receberá notificações e alertas do seu ponto via WhatsApp."
            ok = enviar_texto(celular, texto, tipo='teste_boas_vindas')
            
        elif tipo_msg == 'regra_atraso':
            texto = "⚠️ *Alerta de Atraso:*\nIdentificamos que seu turno iniciou, mas seu ponto de entrada ainda não foi registrado no Secullum."
            ok = enviar_texto(celular, texto, tipo='teste_regra_atraso')
            
        elif tipo_msg == 'regra_hora_extra':
             texto = "⏰ *Alerta de Hora Extra:*\nSeu turno encerrou já faz alguns minutos. Não esqueça de registrar seu ponto de saída!"
             ok = enviar_texto(celular, texto, tipo='teste_regra_he')
             
        elif tipo_msg == 'regra_inconsistencia':
             ontem = (date.today() - timedelta(days=1)).strftime("%d/%m")
             texto = f"📋 *Inconsistências — {ontem}*\n\n⚠️ Batidas inconsistentes (2):\n  • Carlos Eduardo: marcação ímpar\n  • Juliana Silva: marcação ausente\n\n🚫 Ausências (1):\n  • Rodrigo Alves"
             ok = enviar_texto(celular, texto, tipo='teste_relatorio')
             
        elif tipo_msg == 'banco_horas_diario':
             texto = "📊 *Seu resumo diário de Horas:*\n\nSaldo do dia (Ontem): +1.50 horas\nSaldo Acumulado Atual: +14.20 horas\n\nContinue acompanhando seu ponto!"
             ok = enviar_texto(celular, texto, tipo='teste_banco_horas')
             
        else:
            flash('Tipo de mensagem inválido ou não implementado.', 'warning')
            return redirect(url_for('whatsapp.teste'))
            
        if ok:
            flash(f'Mensagem de teste ({tipo_msg}) disparada com sucesso para {celular}!', 'success')
        else:
            flash(f'Falha ao disparar a mensagem ({tipo_msg}). Verifique os logs do WhatsApp ou a conexão com a Mega-API.', 'danger')
            
        return redirect(url_for('whatsapp.teste'))
        
    return render_template('whatsapp/teste.html')
