"""
Serviço de integração com Mega-API (WhatsApp) – Etapa 4.

PRD Antiban (Fase 0 + Fase 1): as funções públicas abaixo NÃO enviam mais
diretamente — elas apenas ENFILEIRAM em FilaEnvioWhatsapp. O envio real
(requests.post) acontece em _despachar_real, chamada exclusivamente por
services/envio_dispatcher.py (que aplica delay/jitter/rate-limit) ou, para
as rotas administrativas de teste/envio manual no painel, de forma síncrona
via o parâmetro imediato=True.

Endpoint REST (obtido via /docs/swagger.json):
  POST https://{host}/rest/sendMessage/{instance_key}/text
       body: { "messageData": { "to": "5527988010899@s.whatsapp.net", "text": "..." } }

  POST https://{host}/rest/sendMessage/{instance_key}/mediaBase64
       body: { "messageData": { "to": "...", "base64": "...", "fileName": "...",
                                "type": "document", "mimeType": "...", "caption": "..." } }

Authorization: Bearer {MEGAAPI_TOKEN}
"""
import base64
import json as _json
import requests
from datetime import datetime, timedelta
from extensions import db
from models import WhatsappLog, FilaEnvioWhatsapp

# Exponential backoff para itens que falham no despacho (PRD 2.0, herdado do
# antigo processar_fila_notificacoes): tentativa 1 -> +5min, 2 -> +15min,
# 3 -> +30min; a partir da 4ª falha, marca 'erro' definitivo.
_BACKOFF_MINUTOS = {1: 5, 2: 15, 3: 30}


def _megaapi():
    from services.config_service import get_megaapi_config
    return get_megaapi_config()


def _base_url() -> str:
    cfg = _megaapi()
    return f'https://{cfg["host"]}/rest/sendMessage/{cfg["instance"]}'


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {_megaapi()["token"]}',
        'Content-Type': 'application/json',
    }


def _fone(celular: str) -> str:
    """Normaliza celular para 5511999999999 (só dígitos — usado para armazenar
    em WhatsappLog/FilaEnvioWhatsapp e comparações/dedup). O sufixo oficial da
    Mega-API é aplicado separadamente por _jid(), só na hora de montar o payload."""
    digits = ''.join(c for c in (celular or '') if c.isdigit())
    if len(digits) == 11:    # DDD + 9 dígitos → adiciona 55
        return f'55{digits}'
    if len(digits) == 13:    # já 5541999999999
        return digits
    return digits


def _jid(celular: str) -> str:
    """Formato oficial da Mega-API para contatos privados (PRD 3.1.4.1):
    sufixo @s.whatsapp.net. Aplicado só ao montar o messageData, nunca ao
    valor armazenado em banco (que fica só com dígitos, via _fone)."""
    digits = celular or ''
    return f'{digits}@s.whatsapp.net' if digits and '@' not in digits else digits


def _configured() -> bool:
    cfg = _megaapi()
    return bool(cfg['token'] and cfg['instance'])


def _eh_primeiro_contato(fone: str) -> bool:
    """PRD Antiban Fase 5: True se nunca houve envio bem-sucedido para este
    número (usado para acionar o lint de link/palavra-gatilho no despacho)."""
    return WhatsappLog.query.filter_by(celular=fone, status='enviado').first() is None


def _preencher_mega_id(log: WhatsappLog, resp) -> None:
    """PRD Antiban Fase 0: captura o id retornado pela Mega-API (campo raiz `id`),
    disponível de forma síncrona na resposta do próprio POST de envio."""
    try:
        log.mega_message_id = resp.json().get('id')
    except Exception:
        pass
    log.atualizado_em = datetime.utcnow()


def _enfileirar(**kwargs) -> FilaEnvioWhatsapp:
    item = FilaEnvioWhatsapp(status='pendente', **kwargs)
    db.session.add(item)
    db.session.commit()
    return item


def _processar_item(item: FilaEnvioWhatsapp) -> bool:
    """Transições de status (pendente → processando → enviado/erro) + despacho
    real. Usada tanto pelo caminho síncrono (imediato=True) quanto pelo
    dispatcher periódico (services/envio_dispatcher.py::processar_proximo)."""
    item.status = 'processando'
    item.tentativas = (item.tentativas or 0) + 1
    db.session.commit()

    ok = _despachar_real(item)

    if ok:
        item.status = 'enviado'
        item.enviado_em = datetime.utcnow()
    else:
        backoff_min = _BACKOFF_MINUTOS.get(item.tentativas)
        if backoff_min is not None:
            # Ainda dentro da janela de retry: volta para 'pendente' com novo enviar_apos
            item.status = 'pendente'
            item.enviar_apos = datetime.utcnow() + timedelta(minutes=backoff_min)
        else:
            item.status = 'erro'
    db.session.commit()
    return ok


def enviar_texto(celular: str, mensagem: str, func_id: str = None,
                 tipo: str = 'saida', tipo_regra: str = None,
                 data_ref=None, prioridade: int = 10, imediato: bool = False) -> bool:
    """Enfileira mensagem de texto para envio via Mega-API.
    Retorno True = aceito na fila (não confirma entrega real) — nenhum
    chamador hoje trata o retorno como confirmação de entrega, então essa
    mudança de semântica é segura.
    imediato=True despacha na hora, sem passar pela fila/rate-limit — uso
    restrito às rotas administrativas de teste/envio manual do painel.
    """
    fone = _fone(celular)
    item = _enfileirar(
        celular=fone, mensagem=mensagem, funcionario_id=func_id,
        tipo=tipo, tipo_regra=tipo_regra, data_referencia=data_ref,
        tipo_msg='texto', prioridade=prioridade, primeiro_contato=_eh_primeiro_contato(fone),
    )
    return _processar_item(item) if imediato else True


def enviar_botoes(celular: str, texto: str, botoes: list,
                  func_id: str = None, tipo: str = 'saida',
                  prioridade: int = 10, imediato: bool = False) -> bool:
    """Enfileira mensagem com botões interativos via Mega-API.

    botoes: lista de dicts {"id": "btn_sim", "title": "👍 Sim, confirmo"}
    Máximo 3 botões por limitação do WhatsApp.

    Fallback automático (se a API recusar) para texto simples numerado é
    tratado no despacho real (_despachar_botoes), não aqui.
    """
    fone = _fone(celular)
    item = _enfileirar(
        celular=fone, mensagem=texto, funcionario_id=func_id, tipo=tipo,
        tipo_msg='botoes', interativo_json=_json.dumps({'botoes': botoes}),
        prioridade=prioridade, primeiro_contato=_eh_primeiro_contato(fone),
    )
    return _processar_item(item) if imediato else True


def enviar_menu_lista(celular: str, texto: str, titulo_botao: str,
                      secoes: list, func_id: str = None, tipo: str = 'saida',
                      prioridade: int = 10, imediato: bool = False) -> bool:
    """Enfileira List Message (menu) via Mega-API — ideal para > 3 opções.

    secoes: lista de dicts:
      {"title": "Seção", "rows": [{"id": "op1", "title": "Opção 1", "description": "..."}]}

    Fallback automático para texto numerado é tratado no despacho real
    (_despachar_lista), não aqui.
    """
    fone = _fone(celular)
    item = _enfileirar(
        celular=fone, mensagem=texto, funcionario_id=func_id, tipo=tipo,
        tipo_msg='lista',
        interativo_json=_json.dumps({'titulo_botao': titulo_botao, 'secoes': secoes}),
        prioridade=prioridade, primeiro_contato=_eh_primeiro_contato(fone),
    )
    return _processar_item(item) if imediato else True


def enviar_msg(celular: str, texto: str,
               tipo_msg: str = 'texto',
               interativo_json: str = None,
               func_id: str = None,
               tipo: str = 'saida',
               tipo_regra: str = None,
               data_ref=None,
               prioridade: int = 10,
               imediato: bool = False) -> bool:
    """Dispatcher unificado: enfileira texto, botões ou lista dependendo de tipo_msg.

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
                return enviar_botoes(celular, texto, botoes, func_id=func_id, tipo=tipo,
                                     prioridade=prioridade, imediato=imediato)
        except Exception:
            pass  # fallback para texto

    elif tipo_msg == 'lista' and interativo_json:
        try:
            dados = _json.loads(interativo_json)
            titulo_botao = dados.get('titulo_botao', 'Ver opções')
            secoes = dados.get('secoes', [])
            if secoes:
                return enviar_menu_lista(celular, texto, titulo_botao, secoes,
                                         func_id=func_id, tipo=tipo,
                                         prioridade=prioridade, imediato=imediato)
        except Exception:
            pass  # fallback para texto

    return enviar_texto(celular, texto, func_id=func_id, tipo=tipo,
                        tipo_regra=tipo_regra, data_ref=data_ref,
                        prioridade=prioridade, imediato=imediato)


def baixar_midia(url: str) -> bytes | None:
    """Baixa mídia (imagem/PDF) de uma URL da Mega-API.
    Retorna bytes ou None em caso de falha.
    """
    try:
        headers = _headers() if _megaapi()['host'] in url else {}
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def enviar_documento(celular: str, pdf_bytes: bytes, filename: str,
                     caption: str = '', func_id: str = None,
                     tipo: str = 'espelho', tipo_regra: str = None,
                     data_ref=None, prioridade: int = 10, imediato: bool = False) -> bool:
    """RF4.4 – Enfileira envio de PDF via Mega-API (mediaBase64)."""
    fone = _fone(celular)
    item = _enfileirar(
        celular=fone, mensagem=caption or filename, funcionario_id=func_id,
        tipo=tipo, tipo_regra=tipo_regra, data_referencia=data_ref,
        tipo_msg='documento',
        interativo_json=_json.dumps({
            'base64': base64.b64encode(pdf_bytes).decode(),
            'fileName': filename,
            'mimeType': 'application/pdf',
            'caption': caption,
        }),
        prioridade=prioridade, primeiro_contato=_eh_primeiro_contato(fone),
    )
    return _processar_item(item) if imediato else True


def _despachar_pergunta_optin(item: FilaEnvioWhatsapp, pergunta: str) -> bool:
    """PRD Antiban Fase 4: envia a pergunta de opt-in (não o conteúdo real do
    item) e grava um WhatsappLog próprio para essa pergunta. Chamada
    exclusivamente por services/envio_dispatcher.py::_iniciar_optin."""
    log = WhatsappLog(
        funcionario_id=item.funcionario_id,
        tipo='optin_pergunta',
        tipo_regra=item.tipo_regra,
        data_referencia=item.data_referencia,
        mensagem=pergunta,
        celular=item.celular,
        status='enviado',
        criado_em=datetime.utcnow(),
    )
    db.session.add(log)
    if not _configured():
        log.status = 'sem_config'
        db.session.commit()
        return False
    try:
        return _post_texto(item.celular, pergunta, log)
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False


# ── Despacho real (só chamado por _processar_item) ───────────────────────────

def _post_texto(fone: str, mensagem: str, log: WhatsappLog) -> bool:
    payload = {'messageData': {'to': _jid(fone), 'text': mensagem}}
    resp = requests.post(f'{_base_url()}/text', json=payload, headers=_headers(), timeout=10)
    ok = resp.status_code in (200, 201)
    if ok:
        log.status = 'enviado'
        _preencher_mega_id(log, resp)
    else:
        log.status = f'erro_{resp.status_code}'
        log.mensagem = f'[ERRO {resp.status_code}] {resp.text[:200]} | msg: {mensagem}'
    db.session.commit()
    return ok


def _despachar_botoes(item: FilaEnvioWhatsapp, fone: str, log: WhatsappLog) -> bool:
    dados = _json.loads(item.interativo_json or '{}')
    botoes = dados.get('botoes', [])
    payload = {
        'messageData': {
            'to': _jid(fone),
            'title': item.mensagem,
            'buttons': [
                {'buttonId': b['id'], 'buttonText': {'displayText': b['title']}, 'type': 1}
                for b in botoes
            ],
            'headerType': 1,
        }
    }
    resp = requests.post(f'{_base_url()}/buttons', json=payload, headers=_headers(), timeout=10)
    if resp.status_code in (200, 201):
        log.status = 'enviado'
        _preencher_mega_id(log, resp)
        db.session.commit()
        return True

    # Fallback: envia como texto numerado (direto, sem voltar para a fila)
    opcoes = '\n'.join(f'{i+1}. {b["title"]}' for i, b in enumerate(botoes))
    fallback = f'{item.mensagem}\n\n{opcoes}'
    log.mensagem = f'[fallback texto] {fallback}'
    log.status = 'fallback_texto'
    db.session.commit()
    return _post_texto(fone, fallback, log)


def _despachar_lista(item: FilaEnvioWhatsapp, fone: str, log: WhatsappLog) -> bool:
    dados = _json.loads(item.interativo_json or '{}')
    titulo_botao = dados.get('titulo_botao', 'Ver opções')
    secoes = dados.get('secoes', [])
    todas_opcoes = [r for s in secoes for r in s.get('rows', [])]
    payload = {
        'messageData': {
            'to': _jid(fone),
            'text': item.mensagem,
            'buttonText': titulo_botao,
            'sections': secoes,
        }
    }
    resp = requests.post(f'{_base_url()}/listMessage', json=payload, headers=_headers(), timeout=10)
    if resp.status_code in (200, 201):
        log.status = 'enviado'
        _preencher_mega_id(log, resp)
        db.session.commit()
        return True

    # Fallback: texto numerado (direto, sem voltar para a fila)
    opcoes = '\n'.join(f'{i+1}. {r["title"]}' for i, r in enumerate(todas_opcoes))
    fallback = f'{item.mensagem}\n\n{opcoes}\n\n_(Digite o número da opção)_'
    log.mensagem = f'[fallback texto] {fallback}'
    log.status = 'fallback_texto'
    db.session.commit()
    return _post_texto(fone, fallback, log)


def _despachar_documento(item: FilaEnvioWhatsapp, fone: str, log: WhatsappLog) -> bool:
    dados = _json.loads(item.interativo_json or '{}')
    payload = {
        'messageData': {
            'to':       _jid(fone),
            'base64':   dados.get('base64', ''),
            'fileName': dados.get('fileName', 'documento.pdf'),
            'type':     'document',
            'mimeType': dados.get('mimeType', 'application/pdf'),
            'caption':  dados.get('caption', ''),
        }
    }
    resp = requests.post(f'{_base_url()}/mediaBase64', json=payload, headers=_headers(), timeout=30)
    ok = resp.status_code in (200, 201)
    log.status = 'enviado' if ok else f'erro_{resp.status_code}'
    if ok:
        _preencher_mega_id(log, resp)
    db.session.commit()
    return ok


def _despachar_real(item: FilaEnvioWhatsapp) -> bool:
    """Chamada exclusivamente por _processar_item. Faz o requests.post real e
    grava o resultado em WhatsappLog (a FilaEnvioWhatsapp só guarda o pedido)."""
    fone = item.celular
    log = WhatsappLog(
        funcionario_id=item.funcionario_id,
        tipo=item.tipo,
        tipo_regra=item.tipo_regra,
        data_referencia=item.data_referencia,
        mensagem=item.mensagem,
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
        if item.tipo_msg == 'botoes':
            return _despachar_botoes(item, fone, log)
        if item.tipo_msg == 'lista':
            return _despachar_lista(item, fone, log)
        if item.tipo_msg == 'documento':
            return _despachar_documento(item, fone, log)
        return _post_texto(fone, item.mensagem, log)
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False
