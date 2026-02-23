"""
Serviço de integração com Mega-API (WhatsApp) – Etapa 4.

Endpoint REST correto (obtido via /docs/swagger.json):
  POST https://{host}/rest/sendMessage/{instance_key}/text
       body: { "messageData": { "to": "5527988010899", "text": "..." } }

  POST https://{host}/rest/sendMessage/{instance_key}/mediaBase64
       body: { "messageData": { "to": "...", "base64": "...", "fileName": "...",
                                "type": "document", "mimeType": "...", "caption": "..." } }

Authorization: Bearer {MEGAAPI_TOKEN}
"""
import base64
import os
import requests
from datetime import datetime
from extensions import db
from models import WhatsappLog


MEGAAPI_HOST     = os.getenv('MEGAAPI_HOST', 'apistart01.megaapi.com.br')
MEGAAPI_INSTANCE = os.getenv('MEGAAPI_INSTANCE', '')
MEGAAPI_TOKEN    = os.getenv('MEGAAPI_TOKEN', '')


def _base_url() -> str:
    return f'https://{MEGAAPI_HOST}/rest/sendMessage/{MEGAAPI_INSTANCE}'


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {MEGAAPI_TOKEN}',
        'Content-Type': 'application/json',
    }


def _fone(celular: str) -> str:
    """Normaliza celular para 5511999999999 (sem @s.whatsapp.net)."""
    digits = ''.join(c for c in (celular or '') if c.isdigit())
    if len(digits) == 11:    # DDD + 9 dígitos → adiciona 55
        return f'55{digits}'
    if len(digits) == 13:    # já 5541999999999
        return digits
    return digits


def _configured() -> bool:
    return bool(MEGAAPI_TOKEN and MEGAAPI_INSTANCE)


def enviar_texto(celular: str, mensagem: str, func_id: str = None, tipo: str = 'saida') -> bool:
    """Envia mensagem de texto via Mega-API e registra o log."""
    fone = _fone(celular)
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        mensagem=mensagem,
        celular=fone,
        status='enviado',
        criado_em=datetime.utcnow(),
    )
    db.session.add(log)

    if not _configured():
        log.status = 'sem_config'
        db.session.commit()
        return False

    try:
        payload = {
            'messageData': {
                'to': fone,          # sem @s.whatsapp.net
                'text': mensagem,
            }
        }
        resp = requests.post(
            f'{_base_url()}/text',
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        if ok:
            log.status = 'enviado'
        else:
            log.status = f'erro_{resp.status_code}'
            log.mensagem = f'[ERRO {resp.status_code}] {resp.text[:200]} | msg: {mensagem}'
        db.session.commit()
        return ok
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False


def enviar_documento(celular: str, pdf_bytes: bytes, filename: str,
                     caption: str = '', func_id: str = None, tipo: str = 'espelho') -> bool:
    """RF4.4 – Envia PDF via Mega-API (mediaBase64) e registra log."""
    fone = _fone(celular)
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        mensagem=caption or filename,
        celular=fone,
        status='enviado',
        criado_em=datetime.utcnow(),
    )
    db.session.add(log)

    if not _configured():
        log.status = 'sem_config'
        db.session.commit()
        return False

    try:
        b64 = base64.b64encode(pdf_bytes).decode()
        payload = {
            'messageData': {
                'to':       fone,
                'base64':   b64,
                'fileName': filename,
                'type':     'document',
                'mimeType': 'application/pdf',
                'caption':  caption,
            }
        }
        resp = requests.post(
            f'{_base_url()}/mediaBase64',
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        ok = resp.status_code in (200, 201)
        log.status = 'enviado' if ok else f'erro_{resp.status_code}'
        db.session.commit()
        return ok
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False
