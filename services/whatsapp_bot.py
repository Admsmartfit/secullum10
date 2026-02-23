"""
Serviço de integração com Mega-API (WhatsApp) – Etapa 4.
"""
import base64
import os
import requests
from datetime import datetime
from extensions import db
from models import WhatsappLog


MEGAAPI_TOKEN = os.getenv('MEGAAPI_TOKEN', '')
MEGAAPI_INSTANCE = os.getenv('MEGAAPI_INSTANCE', '')
MEGAAPI_BASE = f'https://api.megaapi.com.br/rest/sendMessage/{MEGAAPI_INSTANCE}'


def _fone(celular: str) -> str:
    """Normaliza celular para formato internacional 5541999999999."""
    digits = ''.join(c for c in (celular or '') if c.isdigit())
    if len(digits) == 11:          # 41999999999
        return f'55{digits}'
    if len(digits) == 13:          # 5541999999999 – já ok
        return digits
    return digits


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

    if not MEGAAPI_TOKEN or not MEGAAPI_INSTANCE:
        log.status = 'sem_config'
        db.session.commit()
        return False

    try:
        payload = {
            'messageData': {
                'to': f'{fone}@s.whatsapp.net',
                'text': mensagem,
            }
        }
        headers = {'Authorization': f'Bearer {MEGAAPI_TOKEN}', 'Content-Type': 'application/json'}
        resp = requests.post(f'{MEGAAPI_BASE}/sendText', json=payload, headers=headers, timeout=10)
        ok = resp.status_code == 200
        log.status = 'enviado' if ok else f'erro_{resp.status_code}'
        db.session.commit()
        return ok
    except Exception as e:
        log.status = f'erro: {str(e)[:50]}'
        db.session.commit()
        return False


def enviar_documento(celular: str, pdf_bytes: bytes, filename: str,
                     caption: str = '', func_id: str = None, tipo: str = 'espelho') -> bool:
    """RF4.4 – Envia PDF via Mega-API (sendDocument) e registra log."""
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

    if not MEGAAPI_TOKEN or not MEGAAPI_INSTANCE:
        log.status = 'sem_config'
        db.session.commit()
        return False

    try:
        b64 = base64.b64encode(pdf_bytes).decode()
        payload = {
            'messageData': {
                'to': f'{fone}@s.whatsapp.net',
                'document': b64,
                'filename': filename,
                'caption': caption,
                'mimetype': 'application/pdf',
            }
        }
        headers = {'Authorization': f'Bearer {MEGAAPI_TOKEN}', 'Content-Type': 'application/json'}
        resp = requests.post(f'{MEGAAPI_BASE}/sendDocument', json=payload, headers=headers, timeout=30)
        ok = resp.status_code == 200
        log.status = 'enviado' if ok else f'erro_{resp.status_code}'
        db.session.commit()
        return ok
    except Exception as e:
        log.status = f'erro: {str(e)[:50]}'
        db.session.commit()
        return False
