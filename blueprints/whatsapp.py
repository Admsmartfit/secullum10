import hmac, hashlib, os
from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from extensions import db
from models import WhatsappLog, Funcionario, AlocacaoDiaria, UnidadeLider

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

MEGAAPI_SECRET = os.getenv('MEGAAPI_SECRET', '')
GESTOR_CELULAR = os.getenv('GESTOR_CELULAR', '')


def _celular_lider(func: 'Funcionario') -> str:
    """Retorna o celular do líder da unidade do funcionário.
    Fallback: GESTOR_CELULAR global do .env."""
    if func and func.departamento:
        ul = UnidadeLider.query.filter_by(departamento=func.departamento).first()
        if ul and ul.celular_lider:
            return ul.celular_lider
    return GESTOR_CELULAR


def _validar_hmac(payload_bytes: bytes, signature: str) -> bool:
    """Valida assinatura HMAC-SHA256 do webhook Mega-API (RF4.1)."""
    if not MEGAAPI_SECRET:
        return True  # em dev, aceitar sem validação
    expected = hmac.new(MEGAAPI_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()
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


def _processar_mensagem(data: dict):
    """RF4.3 – Processa mensagem de texto ou áudio recebida.
    - SIM: confirma check-in prévio na alocação do dia
    - NÃO/NAO: salva como ausência justificada
    - Áudio: transcreve via Whisper e reprocessa como texto
    - Texto livre: salva e notifica gestor
    """
    from services.whatsapp_bot import enviar_texto

    celular = data.get('from', '').replace('@s.whatsapp.net', '')
    tipo_msg = data.get('type', 'text')  # text | audio | ptt

    # ── Transcrição de áudio (RF4.3) ──────────────────────────────────────────
    if tipo_msg in ('audio', 'ptt'):
        texto = _transcrever_audio(data)
        if not texto:
            return
    else:
        texto = (data.get('body') or data.get('text') or '').strip()

    if not celular or not texto:
        return

    # ── Identificar funcionário pelo celular ──────────────────────────────────
    digits = ''.join(c for c in celular if c.isdigit())[-11:]
    func = Funcionario.query.filter(
        Funcionario.celular.like(f'%{digits[-8:]}%')
    ).first()

    log = WhatsappLog(
        funcionario_id=func.id if func else None,
        tipo='entrada',
        mensagem=texto,
        celular=celular,
        status='recebido',
        criado_em=datetime.utcnow(),
    )
    db.session.add(log)
    db.session.commit()

    if not func:
        return

    resposta_upper = texto.upper().strip()

    # ── RF4.5: SIM → confirma check-in prévio ─────────────────────────────────
    if resposta_upper in ('SIM', 'S', '1'):
        hoje = date.today()
        aloc = AlocacaoDiaria.query.filter_by(funcionario_id=func.id, data=hoje).first()
        if aloc and not aloc.pre_checkin:
            aloc.pre_checkin = True
            db.session.commit()
        enviar_texto(
            celular=func.celular,
            mensagem=f'Perfeito, {func.nome.split()[0]}! Presença confirmada. Bom turno!',
            func_id=func.id,
            tipo='checkin_confirmado',
        )
        return

    # ── NÃO → registra ausência justificada e notifica líder da unidade ────────
    if resposta_upper in ('NÃO', 'NAO', 'N', '0'):
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=f'⚠️ {func.nome} confirmou AUSÊNCIA hoje.',
                func_id=func.id,
                tipo='ausencia_confirmada',
            )
        enviar_texto(
            celular=func.celular,
            mensagem='Entendido! Sua ausência foi registrada. Qualquer problema, entre em contato com o RH.',
            func_id=func.id,
            tipo='ausencia_confirmada',
        )
        return

    # ── Texto livre → encaminha ao líder da unidade (RF4.3) ──────────────────
    lider_cel = _celular_lider(func)
    if lider_cel:
        enviar_texto(
            celular=lider_cel,
            mensagem=f'💬 Mensagem de *{func.nome}*:\n"{texto}"',
            func_id=func.id,
            tipo='notificacao_gestor',
        )


def _transcrever_audio(data: dict) -> str:
    """RF4.3 – Transcreve áudio via OpenAI Whisper API.
    Retorna texto transcrito ou '' se falhar ou não configurado.
    """
    import requests as req_lib
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
    logs = (
        WhatsappLog.query
        .order_by(WhatsappLog.criado_em.desc())
        .limit(200)
        .all()
    )
    funcionarios = (
        Funcionario.query
        .filter(Funcionario.celular.isnot(None), Funcionario.ativo == True)
        .order_by(Funcionario.nome)
        .all()
    )
    return render_template('whatsapp/logs.html', logs=logs, funcionarios=funcionarios)


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
