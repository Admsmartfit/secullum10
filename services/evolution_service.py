"""
Cliente HTTP dedicado à Evolution API v2 — substitui a Mega-API como
transporte de envio de WhatsApp do secullum10 (migração Evolution API).

Isola toda a integração HTTP num único módulo: services/whatsapp_bot.py
(camada de fila/log) chama estas funções em vez de montar requests.post
diretamente, mantendo o mesmo desenho que já existia para a Mega-API.

Auth: header `apikey: {EVOLUTION_API_KEY}` (diferente do
`Authorization: Bearer` usado pela Mega-API).

Endpoints (Evolution API v2):
  POST /message/sendText/{instance}      body: {"number": "...", "text": "..."}
  POST /message/sendButtons/{instance}   body: {"number": "...", "title": "...",
                                                 "description": "...", "buttons": [...]}
  POST /message/sendMedia/{instance}     body: {"number": "...", "mediatype": "document",
                                                 "media": "<base64>", "fileName": "...",
                                                 "caption": "..."}
  POST /chat/sendPresence/{instance}     body: {"number": "...", "presence": "composing",
                                                 "delay": <ms>}

`number` normalizado só com dígitos (sem sufixo `@s.whatsapp.net` no corpo —
a Evolution resolve o JID internamente a partir do número puro). Nota de
risco: nomes de endpoint/payload seguem a documentação levantada para esta
instância; validar com um teste manual (curl/script) contra a instância real
antes de depender deste módulo em produção (ver plano de verificação).
"""
import requests

_TIMEOUT_PADRAO = 10
_TIMEOUT_MIDIA = 30


def _config() -> dict:
    from services.config_service import get_evolutionapi_config
    return get_evolutionapi_config()


def _configured() -> bool:
    cfg = _config()
    return bool(cfg['host'] and cfg['instance'] and cfg['api_key'])


def _base_url() -> str:
    cfg = _config()
    return cfg['host'].rstrip('/')


def _instance() -> str:
    return _config()['instance']


def _headers() -> dict:
    return {
        'apikey': _config()['api_key'],
        'Content-Type': 'application/json',
    }


def send_text_message(number: str, text: str) -> requests.Response:
    """POST /message/sendText/{instance}"""
    payload = {'number': number, 'text': text}
    return requests.post(
        f'{_base_url()}/message/sendText/{_instance()}',
        json=payload, headers=_headers(), timeout=_TIMEOUT_PADRAO,
    )


def send_interactive_buttons(number: str, title: str, body: str, buttons: list) -> requests.Response:
    """POST /message/sendButtons/{instance}

    buttons: lista de dicts {"id": "opt_sim", "title": "Sim"} (máx. 3, limite do WhatsApp).
    """
    payload = {
        'number': number,
        'title': title,
        'description': body,
        'buttons': [{'id': b['id'], 'title': b['title']} for b in buttons],
    }
    return requests.post(
        f'{_base_url()}/message/sendButtons/{_instance()}',
        json=payload, headers=_headers(), timeout=_TIMEOUT_PADRAO,
    )


def send_media_message(number: str, media_base64: str, file_name: str,
                        mime_type: str, caption: str = '') -> requests.Response:
    """POST /message/sendMedia/{instance}"""
    mediatype = 'document' if mime_type != 'image/jpeg' and mime_type != 'image/png' else 'image'
    payload = {
        'number': number,
        'mediatype': mediatype,
        'mimetype': mime_type,
        'media': media_base64,
        'fileName': file_name,
        'caption': caption,
    }
    return requests.post(
        f'{_base_url()}/message/sendMedia/{_instance()}',
        json=payload, headers=_headers(), timeout=_TIMEOUT_MIDIA,
    )


def send_presence(number: str, state: str = 'composing', delay_ms: int = 2000) -> None:
    """POST /chat/sendPresence/{instance} — dispara o indicador "digitando..."
    visível para o destinatário. Best-effort: falha silenciosamente (uma
    presença que não chega não deve impedir o envio real da mensagem)."""
    try:
        requests.post(
            f'{_base_url()}/chat/sendPresence/{_instance()}',
            json={'number': number, 'presence': state, 'delay': delay_ms},
            headers=_headers(), timeout=_TIMEOUT_PADRAO,
        )
    except Exception:
        pass
