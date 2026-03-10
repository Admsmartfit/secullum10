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
import json as _json
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


def enviar_texto(celular: str, mensagem: str, func_id: str = None, 
                 tipo: str = 'saida', tipo_regra: str = None, 
                 data_ref=None) -> bool:
    """Envia mensagem de texto via Mega-API e registra o log."""
    fone = _fone(celular)
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        tipo_regra=tipo_regra,
        data_referencia=data_ref,
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


def enviar_botoes(celular: str, texto: str, botoes: list,
                  func_id: str = None, tipo: str = 'saida') -> bool:
    """Envia mensagem com botões interativos via Mega-API.

    botoes: lista de dicts {"id": "btn_sim", "title": "👍 Sim, confirmo"}
    Máximo 3 botões por limitação do WhatsApp.

    Fallback automático: se a API recusar (ex: conta não suporta), envia
    texto simples numerado.
    """
    fone = _fone(celular)
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        mensagem=texto,
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
                'to': fone,
                'title': texto,
                'buttons': [
                    {'buttonId': b['id'], 'buttonText': {'displayText': b['title']}, 'type': 1}
                    for b in botoes
                ],
                'headerType': 1,
            }
        }
        resp = requests.post(
            f'{_base_url()}/buttons',
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        if ok:
            log.status = 'enviado'
            db.session.commit()
            return True

        # Fallback: envia como texto numerado
        opcoes = '\n'.join(f'{i+1}. {b["title"]}' for i, b in enumerate(botoes))
        fallback = f'{texto}\n\n{opcoes}'
        log.mensagem = f'[fallback texto] {fallback}'
        log.status = 'fallback_texto'
        db.session.commit()
        return enviar_texto(celular, fallback, func_id=func_id, tipo=tipo)
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False


def enviar_menu_lista(celular: str, texto: str, titulo_botao: str,
                      secoes: list, func_id: str = None, tipo: str = 'saida') -> bool:
    """Envia List Message (menu) via Mega-API — ideal para > 3 opções.

    secoes: lista de dicts:
      {"title": "Seção", "rows": [{"id": "op1", "title": "Opção 1", "description": "..."}]}

    Fallback: envia texto numerado com todas as opções.
    """
    fone = _fone(celular)
    todas_opcoes = [r for s in secoes for r in s.get('rows', [])]
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        mensagem=texto,
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
                'to': fone,
                'text': texto,
                'buttonText': titulo_botao,
                'sections': secoes,
            }
        }
        resp = requests.post(
            f'{_base_url()}/listMessage',
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        if ok:
            log.status = 'enviado'
            db.session.commit()
            return True

        # Fallback: texto numerado
        opcoes = '\n'.join(f'{i+1}. {r["title"]}' for i, r in enumerate(todas_opcoes))
        fallback = f'{texto}\n\n{opcoes}\n\n_(Digite o número da opção)_'
        log.mensagem = f'[fallback texto] {fallback}'
        log.status = 'fallback_texto'
        db.session.commit()
        return enviar_texto(celular, fallback, func_id=func_id, tipo=tipo)
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False


def enviar_msg(celular: str, texto: str,
               tipo_msg: str = 'texto',
               interativo_json: str = None,
               func_id: str = None,
               tipo: str = 'saida',
               tipo_regra: str = None,
               data_ref=None) -> bool:
    """Dispatcher unificado: envia texto, botões ou lista dependendo de tipo_msg.

    interativo_json (str): JSON serializado com estrutura:
      Botões: {"botoes": [{"id": "btn_1", "title": "Opção 1"}, ...]}
      Lista:  {"titulo_botao": "Ver opções",
               "secoes": [{"title": "Seção", "rows": [{"id": "r1", "title": "Item", "description": "..."}]}]}
    """
    if tipo_msg == 'botoes' and interativo_json:
        try:
            dados = _json.loads(interativo_json)
            botoes = dados.get('botoes', [])
            if botoes:
                return enviar_botoes(celular, texto, botoes, func_id=func_id, tipo=tipo)
        except Exception:
            pass  # fallback para texto

    elif tipo_msg == 'lista' and interativo_json:
        try:
            dados = _json.loads(interativo_json)
            titulo_botao = dados.get('titulo_botao', 'Ver opções')
            secoes = dados.get('secoes', [])
            if secoes:
                return enviar_menu_lista(celular, texto, titulo_botao, secoes,
                                         func_id=func_id, tipo=tipo)
        except Exception:
            pass  # fallback para texto

    return enviar_texto(celular, texto, func_id=func_id, tipo=tipo,
                        tipo_regra=tipo_regra, data_ref=data_ref)


def baixar_midia(url: str) -> bytes | None:
    """Baixa mídia (imagem/PDF) de uma URL da Mega-API.
    Retorna bytes ou None em caso de falha.
    """
    try:
        headers = _headers() if MEGAAPI_HOST in url else {}
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def enviar_documento(celular: str, pdf_bytes: bytes, filename: str,
                     caption: str = '', func_id: str = None, 
                     tipo: str = 'espelho', tipo_regra: str = None,
                     data_ref=None) -> bool:
    """RF4.4 – Envia PDF via Mega-API (mediaBase64) e registra log."""
    fone = _fone(celular)
    log = WhatsappLog(
        funcionario_id=func_id,
        tipo=tipo,
        tipo_regra=tipo_regra,
        data_referencia=data_ref,
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
