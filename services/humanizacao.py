"""Cálculo de duração de digitação humana, compartilhado por:
- services/envio_dispatcher.py (Fase 2: atraso extra não-bloqueante na fila)
- services/whatsapp_bot.py (simulação real de presença 'composing' na Evolution API)

Extraído para módulo próprio para não duplicar a fórmula entre os dois usos.
"""
import random


def calcular_duracao_digitacao(mensagem: str) -> float:
    """ms/caractere + jitter aleatório + piso e teto de segurança — garante que
    NENHUMA mensagem seja despachada instantaneamente (o piso mínimo é o que
    evita que respostas curtas tipo "Ok"/"Sim" saiam em milissegundos, o
    principal indício de automação para o WhatsApp) nem demore tanto que
    pareça travado."""
    from services.config_service import get_setting

    def _cfg_float(chave, env, default):
        return float(get_setting(chave, env, str(default)))

    por_char = _cfg_float('whatsapp_delay_por_caractere_s', 'WA_DELAY_CHAR_S', 0.045)
    jitter_max = _cfg_float('whatsapp_delay_jitter_digitacao_s', 'WA_DELAY_JITTER_DIGITACAO_S', 1.5)
    piso = _cfg_float('whatsapp_delay_extra_min_s', 'WA_DELAY_EXTRA_MIN_S', 1.5)
    teto = _cfg_float('whatsapp_delay_extra_max_s', 'WA_DELAY_EXTRA_MAX_S', 8)

    tempo = len(mensagem or '') * por_char
    tempo += random.uniform(0, jitter_max)
    return max(piso, min(teto, tempo))
