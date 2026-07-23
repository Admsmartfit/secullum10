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
    """Delay adicional proporcional ao tamanho da mensagem (Fase 2 revisada:
    substitui a simulação de presença 'digitando...', que a Mega-API não expõe)."""
    por_char = _cfg_float('whatsapp_delay_por_caractere_s', 'WA_DELAY_CHAR_S', 0.15)
    teto = _cfg_float('whatsapp_delay_extra_max_s', 'WA_DELAY_EXTRA_MAX_S', 10)
    return min(teto, len(mensagem or '') * por_char)


def processar_proximo() -> dict:
    """Processa NO MÁXIMO 1 item da fila por chamada (chamado a cada poucos segundos)."""
    if not _pode_enviar_agora():
        return {'skipped': True, 'motivo': 'rate_limit_ou_desativado'}

    agora = datetime.utcnow()
    item = (FilaEnvioWhatsapp.query
            .filter(
                FilaEnvioWhatsapp.status == 'pendente',
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

    from services.whatsapp_bot import _processar_item
    ok = _processar_item(item)
    return {'enviado': ok, 'item_id': item.id}
