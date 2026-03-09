import hmac, hashlib, os
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from extensions import db
from models import WhatsappLog, Funcionario, AlocacaoDiaria, UnidadeLider, Batida, NotificacaoFila

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

MEGAAPI_SECRET = os.getenv('MEGAAPI_SECRET', '')
GESTOR_CELULAR = os.getenv('GESTOR_CELULAR', '')


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
            mensagem=_bot_msg('bot_msg_sim_func', {'nome': func.nome.split()[0]}),
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
        return

    # ── Texto livre → Justificativa Automática (RF02) ou Encaminha ao Líder ────
    import re
    # Se não for SIM/NÃO e tiver pelo menos 3 palavras, ou for explicitamente uma justificativa
    # Vamos procurar uma batida inconsistente nos últimos 2 dias
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
        # Salva justificativa
        prefixo = f"[WhatsApp {datetime.now().strftime('%d/%m %H:%M')}] "
        nova_just = f"{prefixo}{texto}"
        if ultima_inc.justificativa:
            ultima_inc.justificativa = f"{ultima_inc.justificativa}\n{nova_just}"
        else:
            ultima_inc.justificativa = nova_just
        
        ultima_inc.justificada_via = 'Bot'
        db.session.commit()
        
        # Confirma para o funcionário
        enviar_texto(
            celular=func.celular,
            mensagem=_bot_msg('bot_msg_justificativa_func', {
                'nome': func.nome.split()[0],
                'data': ultima_inc.data.strftime('%d/%m'),
            }),
            func_id=func.id,
            tipo='justificativa_automatica'
        )

        # Notifica o líder (opcional, mas bom pra compliance)
        lider_cel = _celular_lider(func)
        if lider_cel:
            enviar_texto(
                celular=lider_cel,
                mensagem=_bot_msg('bot_msg_justificativa_lider', {
                    'nome': func.nome,
                    'mensagem': texto,
                }),
                func_id=func.id,
                tipo='notificacao_gestor_justificativa'
            )
        return

    # Caso não encontre inconsistência, apenas encaminha ao líder como texto livre
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
