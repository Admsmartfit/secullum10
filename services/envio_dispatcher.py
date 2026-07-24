"""
Camada única de despacho de WhatsApp — PRD Antiban (Fase 1 + Fase 2 revisada).

Aplica delay mínimo + jitter, teto de envios/hora e um atraso adicional
proporcional ao tamanho da mensagem antes de cada envio real. Presença
("digitando...") não é aplicável — a Mega-API não expõe esse endpoint.

Chamado periodicamente (a cada ~5s) por services/auto_sync.py. Processa no
máximo 1 item da fila por chamada — nenhum time.sleep bloqueante aqui.
"""
import random
from datetime import datetime, timedelta

from extensions import db
from models import FilaEnvioWhatsapp


def _cfg_int(chave, env, default):
    from services.config_service import get_setting
    return int(get_setting(chave, env, str(default)))


def _cfg_float(chave, env, default):
    from services.config_service import get_setting
    return float(get_setting(chave, env, str(default)))


def _pode_enviar_agora() -> bool:
    """Verifica intervalo mínimo (+jitter) desde o último envio bem-sucedido e teto por hora."""
    from services.config_service import get_setting
    if get_setting('whatsapp_dispatcher_ativo', 'WA_DISPATCHER_ATIVO', '1') != '1':
        return False

    ultimo = (FilaEnvioWhatsapp.query
              .filter(FilaEnvioWhatsapp.status == 'enviado')
              .order_by(FilaEnvioWhatsapp.enviado_em.desc())
              .first())
    delay_min = _cfg_int('whatsapp_delay_min_s', 'WA_DELAY_MIN_S', 20)
    jitter = random.randint(0, _cfg_int('whatsapp_delay_max_jitter_s', 'WA_DELAY_JITTER_S', 15))
    intervalo_necessario = delay_min + jitter

    if ultimo and ultimo.enviado_em:
        decorrido = (datetime.utcnow() - ultimo.enviado_em).total_seconds()
        if decorrido < intervalo_necessario:
            return False

    limite_hora = datetime.utcnow() - timedelta(hours=1)
    enviados_ultima_hora = FilaEnvioWhatsapp.query.filter(
        FilaEnvioWhatsapp.status == 'enviado',
        FilaEnvioWhatsapp.enviado_em >= limite_hora,
    ).count()
    max_hora = _cfg_int('whatsapp_max_por_hora', 'WA_MAX_HORA', 20)
    return enviados_ultima_hora < max_hora


def _calcular_delay_extra(mensagem: str) -> float:
    """Simula o tempo de digitação humana antes do envio real (Fase 2 revisada:
    substitui a simulação de presença 'digitando...', que a Mega-API não expõe).

    ms/caractere + jitter aleatório + piso e teto de segurança — garante que
    NENHUMA mensagem seja despachada instantaneamente (o piso mínimo é o que
    evita que respostas curtas tipo "Ok"/"Sim" saiam em milissegundos, o
    principal indício de automação para o WhatsApp) nem demore tanto que
    pareça travado.
    """
    por_char = _cfg_float('whatsapp_delay_por_caractere_s', 'WA_DELAY_CHAR_S', 0.045)
    jitter_max = _cfg_float('whatsapp_delay_jitter_digitacao_s', 'WA_DELAY_JITTER_DIGITACAO_S', 1.5)
    piso = _cfg_float('whatsapp_delay_extra_min_s', 'WA_DELAY_EXTRA_MIN_S', 1.5)
    teto = _cfg_float('whatsapp_delay_extra_max_s', 'WA_DELAY_EXTRA_MAX_S', 8)

    tempo = len(mensagem or '') * por_char
    tempo += random.uniform(0, jitter_max)
    return max(piso, min(teto, tempo))


def _lint_problemas(item: FilaEnvioWhatsapp) -> list:
    """PRD Antiban Fase 5: rede de segurança em tempo de execução — bloqueia
    link/palavra-gatilho em mensagens de primeiro contato (a validação
    principal já acontece no cadastro do template, ver blueprints/config_hub.py)."""
    if not item.primeiro_contato:
        return []
    from services.config_service import get_setting
    from services.lint_template import validar_template_primeiro_contato
    palavras = [p for p in get_setting(
        'whatsapp_palavras_gatilho', 'WA_PALAVRAS_GATILHO',
        'promoção,grátis,desconto,clique aqui,imperdível',
    ).split(',') if p.strip()]
    return validar_template_primeiro_contato(item.mensagem, palavras)


def _alertar_bloqueio_lint(item: FilaEnvioWhatsapp, problemas: list) -> None:
    import logging
    logging.getLogger('envio_dispatcher').error(
        f'[bloqueado_lint] item {item.id} bloqueado (primeiro contato): {problemas}'
    )
    import os
    rh_email = os.getenv('RH_EMAIL', '')
    if not rh_email:
        return
    try:
        from flask_mail import Message
        from extensions import mail
        mail.send(Message(
            subject='⚠️ Mensagem de primeiro contato bloqueada (WhatsApp)',
            recipients=[rh_email],
            body=f'Item da fila #{item.id} (celular {item.celular}) foi bloqueado antes do envio:\n'
                 + '\n'.join(problemas) + f'\n\nMensagem:\n{item.mensagem}',
        ))
    except Exception:
        pass


def _iniciar_optin(item: FilaEnvioWhatsapp) -> dict:
    """PRD Antiban Fase 4: envia a pergunta de opt-in em vez do conteúdo real e
    coloca o funcionário em estado de espera (ChatState AGUARDANDO_OPTIN).

    Só se aplica quando o destinatário é o próprio funcionário: o campo
    `funcionario_id` de FilaEnvioWhatsapp indica de quem é o evento, não
    necessariamente quem recebe a mensagem — envios para gestor/RH/telefone
    customizado (notification_processor.py::_enviar) usam o mesmo
    funcionario_id do funcionário, mas vão para outro número, e não há como
    amarrar a resposta de opt-in a um ChatState nesses casos. Quando não se
    aplica, despacha normalmente (sem opt-in)."""
    from models import Funcionario
    from services.whatsapp_bot import _fone, _processar_item
    func = Funcionario.query.get(item.funcionario_id) if item.funcionario_id else None
    if not func or _fone(func.celular or '') != item.celular:
        return {'enviado': _processar_item(item), 'item_id': item.id, 'optin': False}

    from services.config_service import get_setting
    nome = (func.nome or '').split()[0] if func.nome else ''
    assunto = (item.regra.nome if item.regra else '') or 'uma mensagem'
    pergunta = get_setting(
        'whatsapp_optin_texto_padrao', 'WA_OPTIN_TEXTO_PADRAO',
        'Olá {{name}}, tudo bem? Posso te enviar {{assunto}} por aqui?',
    ).replace('{{name}}', nome).replace('{{assunto}}', assunto)

    from services.whatsapp_bot import _despachar_pergunta_optin
    _despachar_pergunta_optin(item, pergunta)

    item.status = 'aguardando_optin'
    db.session.commit()

    from blueprints.whatsapp import _set_state
    _set_state(func.id, 'AGUARDANDO_OPTIN', {'fila_id': item.id})

    return {'aguardando_optin': True, 'item_id': item.id}


def processar_proximo() -> dict:
    """Processa NO MÁXIMO 1 item da fila por chamada (chamado a cada poucos segundos)."""
    if not _pode_enviar_agora():
        return {'skipped': True, 'motivo': 'rate_limit_ou_desativado'}

    agora = datetime.utcnow()
    # 'optin_confirmado' = item que já passou pela pergunta de opt-in e foi
    # aceito pelo funcionário; segue direto para despacho, sem perguntar de novo.
    item = (FilaEnvioWhatsapp.query
            .filter(
                FilaEnvioWhatsapp.status.in_(['pendente', 'optin_confirmado']),
                db.or_(FilaEnvioWhatsapp.enviar_apos.is_(None),
                       FilaEnvioWhatsapp.enviar_apos <= agora),
            )
            .order_by(FilaEnvioWhatsapp.prioridade.asc(), FilaEnvioWhatsapp.criada_em.asc())
            .first())
    if not item:
        return {'skipped': True, 'motivo': 'fila_vazia'}

    # Primeira leitura deste item: aplica o delay extra proporcional ao tamanho
    # (Fase 2), adiando-o na própria fila em vez de bloquear o job com sleep.
    if item.enviar_apos is None:
        delay_extra = _calcular_delay_extra(item.mensagem)
        if delay_extra > 0:
            item.enviar_apos = agora + timedelta(seconds=delay_extra)
            db.session.commit()
            return {'skipped': True, 'motivo': 'delay_extra_aplicado', 'item_id': item.id}

    problemas = _lint_problemas(item)
    if problemas:
        item.status = 'bloqueado_lint'
        db.session.commit()
        _alertar_bloqueio_lint(item, problemas)
        return {'bloqueado_lint': True, 'item_id': item.id, 'problemas': problemas}

    # Opt-in só é avaliado na primeira passagem ('pendente'); um item já
    # 'optin_confirmado' pula direto para o despacho real.
    if item.status == 'pendente' and item.regra_id and item.regra and item.regra.requer_optin:
        return _iniciar_optin(item)

    from services.whatsapp_bot import _processar_item
    ok = _processar_item(item)
    return {'enviado': ok, 'item_id': item.id}
