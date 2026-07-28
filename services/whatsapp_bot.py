"""
Serviço de integração com WhatsApp – Etapa 4.

Transporte: Evolution API (migração a partir da Mega-API — ver
services/evolution_service.py para o cliente HTTP e
MANUAL_INSTALACAO_EVOLUTION_API.md para a infraestrutura). As funções
públicas abaixo NÃO enviam diretamente — elas apenas ENFILEIRAM em
FilaEnvioWhatsapp. O envio real acontece em _despachar_real, chamada
exclusivamente por services/envio_dispatcher.py (que aplica
delay/jitter/rate-limit) ou, para as rotas administrativas de
teste/envio manual no painel, de forma síncrona via o parâmetro
imediato=True.

Blacklist absoluta de opt-out: qualquer número presente em
models.WhatsappBlacklist é rejeitado em TODO envio, sem exceção — ver
_bloqueado(), checado em _despachar_real e _despachar_pergunta_optin (os
dois únicos pontos reais de saída de mensagem).
"""
import base64
import json as _json
from datetime import datetime, timedelta
from extensions import db
from models import WhatsappLog, FilaEnvioWhatsapp, WhatsappBlacklist
from services import evolution_service

# Exponential backoff para itens que falham no despacho (PRD 2.0, herdado do
# antigo processar_fila_notificacoes): tentativa 1 -> +5min, 2 -> +15min,
# 3 -> +30min; a partir da 4ª falha, marca 'erro' definitivo.
_BACKOFF_MINUTOS = {1: 5, 2: 15, 3: 30}


def _fone(celular: str) -> str:
    """Normaliza celular para 5511999999999 (só dígitos — usado para armazenar
    em WhatsappLog/FilaEnvioWhatsapp/WhatsappBlacklist e para o payload
    enviado à Evolution API, que aceita o número puro sem sufixo de JID)."""
    digits = ''.join(c for c in (celular or '') if c.isdigit())
    if len(digits) == 11:    # DDD + 9 dígitos → adiciona 55
        return f'55{digits}'
    if len(digits) == 13:    # já 5541999999999
        return digits
    return digits


def _configured() -> bool:
    return evolution_service._configured()


def _bloqueado(fone: str) -> bool:
    """Blacklist absoluta de opt-out: True se o número já pediu para não
    receber mais mensagens. Sem exceção de cargo/regra — checado nos dois
    pontos reais de saída de mensagem (_despachar_real, _despachar_pergunta_optin)."""
    return WhatsappBlacklist.query.filter_by(celular=fone).first() is not None


def _eh_primeiro_contato(fone: str) -> bool:
    """PRD Antiban Fase 5: True se nunca houve envio bem-sucedido para este
    número (usado para acionar o lint de link/palavra-gatilho no despacho)."""
    return WhatsappLog.query.filter_by(celular=fone, status='enviado').first() is None


def _preencher_mega_id(log: WhatsappLog, resp) -> None:
    """PRD Antiban Fase 0: captura o id da mensagem enviada, disponível de
    forma síncrona na resposta do próprio POST de envio. A Evolution API
    normalmente aninha em `key.id`; mantém o fallback para `id` na raiz
    (formato antigo da Mega-API) por segurança, já que o shape exato desta
    instância ainda não foi validado em produção (ver plano de verificação)."""
    try:
        body = resp.json()
        log.mega_message_id = (body.get('key') or {}).get('id') or body.get('id')
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
                 data_ref=None, prioridade: int = 10, imediato: bool = False,
                 regra_id: int = None) -> bool:
    """Enfileira mensagem de texto para envio via Evolution API.
    Retorno True = aceito na fila (não confirma entrega real) — nenhum
    chamador hoje trata o retorno como confirmação de entrega, então essa
    mudança de semântica é segura.
    imediato=True despacha na hora, sem passar pela fila/rate-limit — uso
    restrito às rotas administrativas de teste/envio manual do painel.
    regra_id: amarra o item à NotificationRule de origem — necessário para o
    opt-in (Fase 4) e o lint (Fase 5) que dependem de `item.regra`; sem isso,
    o item nunca é elegível para opt-in mesmo com requer_optin=True na regra.
    """
    fone = _fone(celular)
    item = _enfileirar(
        celular=fone, mensagem=mensagem, funcionario_id=func_id,
        tipo=tipo, tipo_regra=tipo_regra, data_referencia=data_ref,
        tipo_msg='texto', prioridade=prioridade, primeiro_contato=_eh_primeiro_contato(fone),
        regra_id=regra_id,
    )
    return _processar_item(item) if imediato else True


def enviar_botoes(celular: str, texto: str, botoes: list,
                  func_id: str = None, tipo: str = 'saida',
                  prioridade: int = 10, imediato: bool = False,
                  regra_id: int = None) -> bool:
    """Enfileira mensagem com botões interativos via Evolution API.

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
        regra_id=regra_id,
    )
    return _processar_item(item) if imediato else True


def enviar_menu_lista(celular: str, texto: str, titulo_botao: str,
                      secoes: list, func_id: str = None, tipo: str = 'saida',
                      prioridade: int = 10, imediato: bool = False,
                      regra_id: int = None) -> bool:
    """Enfileira List Message (menu) via Evolution API — ideal para > 3 opções.

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
        regra_id=regra_id,
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
               imediato: bool = False,
               regra_id: int = None) -> bool:
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
                                     prioridade=prioridade, imediato=imediato, regra_id=regra_id)
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
                                         prioridade=prioridade, imediato=imediato, regra_id=regra_id)
        except Exception:
            pass  # fallback para texto

    return enviar_texto(celular, texto, func_id=func_id, tipo=tipo,
                        tipo_regra=tipo_regra, data_ref=data_ref,
                        prioridade=prioridade, imediato=imediato, regra_id=regra_id)


def baixar_midia(url: str) -> bytes | None:
    """Baixa mídia (imagem/PDF) de uma URL retornada pela Evolution API no
    webhook de mensagem recebida. Retorna bytes ou None em caso de falha.
    """
    import requests
    from services.config_service import get_evolutionapi_config
    try:
        cfg = get_evolutionapi_config()
        headers = {'apikey': cfg['api_key']} if cfg['host'] and cfg['host'] in url else {}
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def enviar_documento(celular: str, pdf_bytes: bytes, filename: str,
                     caption: str = '', func_id: str = None,
                     tipo: str = 'espelho', tipo_regra: str = None,
                     data_ref=None, prioridade: int = 10, imediato: bool = False,
                     regra_id: int = None) -> bool:
    """RF4.4 – Enfileira envio de PDF via Evolution API (sendMedia)."""
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
        regra_id=regra_id,
    )
    return _processar_item(item) if imediato else True


def _despachar_pergunta_optin(item: FilaEnvioWhatsapp, pergunta: str,
                              botoes: list = None) -> bool:
    """PRD Antiban Fase 4: envia a pergunta de opt-in (não o conteúdo real do
    item) e grava um WhatsappLog próprio para essa pergunta. Chamada
    exclusivamente por services/envio_dispatcher.py::_iniciar_optin.

    botoes: se fornecido (ver blueprints/whatsapp.py::_iniciar_optin, IDs
    'opt_sim'/'opt_nao'), envia como botões nativos Sim/Não; senão, texto
    simples com instrução de resposta livre."""
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

    if _bloqueado(item.celular):
        log.status = 'bloqueado'
        db.session.commit()
        return False

    if not _configured():
        log.status = 'sem_config'
        db.session.commit()
        return False
    try:
        if botoes:
            return _post_botoes_optin(item.celular, pergunta, botoes, log)
        return _post_texto(item.celular, pergunta, log)
    except Exception as e:
        log.status = f'erro: {str(e)[:80]}'
        db.session.commit()
        return False


def _post_botoes_optin(fone: str, pergunta: str, botoes: list, log: WhatsappLog) -> bool:
    """Envia a pergunta de opt-in com botões nativos Sim/Não (Evolution API).
    Fallback para texto com instrução livre ("responda sim/não") se a
    Evolution recusar os botões — mesmo padrão de _despachar_botoes."""
    resp = evolution_service.send_interactive_buttons(fone, 'Confirmação', pergunta, botoes)
    if resp.status_code in (200, 201):
        log.status = 'enviado'
        _preencher_mega_id(log, resp)
        db.session.commit()
        return True

    opcoes = ' / '.join(b['title'] for b in botoes)
    fallback = f'{pergunta}\n\nResponda: {opcoes}'
    log.mensagem = f'[fallback texto] {fallback}'
    log.status = 'fallback_texto'
    db.session.commit()
    return _post_texto(fone, fallback, log)


# ── Despacho real (só chamado por _processar_item) ───────────────────────────

def _post_texto(fone: str, mensagem: str, log: WhatsappLog) -> bool:
    from services.humanizacao import calcular_duracao_digitacao
    duracao = calcular_duracao_digitacao(mensagem)
    evolution_service.send_presence(fone, 'composing', delay_ms=int(duracao * 1000))
    import time
    time.sleep(duracao)

    resp = evolution_service.send_text_message(fone, mensagem)
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
    resp = evolution_service.send_interactive_buttons(fone, 'Confirmação', item.mensagem, botoes)
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
    """A Evolution API não tem um endpoint de List Message equivalente ao da
    Mega-API — despacha como botões nativos se houver <= 3 opções ao todo, ou
    cai direto para texto numerado (mesmo fallback que já existia)."""
    dados = _json.loads(item.interativo_json or '{}')
    titulo_botao = dados.get('titulo_botao', 'Ver opções')
    secoes = dados.get('secoes', [])
    todas_opcoes = [r for s in secoes for r in s.get('rows', [])]

    if 0 < len(todas_opcoes) <= 3:
        botoes = [{'id': r['id'], 'title': r['title']} for r in todas_opcoes]
        resp = evolution_service.send_interactive_buttons(fone, titulo_botao, item.mensagem, botoes)
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
    resp = evolution_service.send_media_message(
        fone,
        media_base64=dados.get('base64', ''),
        file_name=dados.get('fileName', 'documento.pdf'),
        mime_type=dados.get('mimeType', 'application/pdf'),
        caption=dados.get('caption', ''),
    )
    ok = resp.status_code in (200, 201)
    log.status = 'enviado' if ok else f'erro_{resp.status_code}'
    if ok:
        _preencher_mega_id(log, resp)
    db.session.commit()
    return ok


def _despachar_real(item: FilaEnvioWhatsapp) -> bool:
    """Chamada exclusivamente por _processar_item. Faz o envio real via
    Evolution API e grava o resultado em WhatsappLog (a FilaEnvioWhatsapp só
    guarda o pedido)."""
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

    if _bloqueado(fone):
        log.status = 'bloqueado'
        db.session.commit()
        return False

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
