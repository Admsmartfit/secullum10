"""
Motor de variação de texto (Spintax) — PRD Antiban Fase 3.
Evita que dois funcionários recebam o texto exatamente idêntico no mesmo dia.
"""
import random
import re

_SPINTAX_RE = re.compile(r'\{([^{}]+)\}')


def resolver_spintax(texto: str) -> str:
    """Resolve {opcao1|opcao2|opcao3} recursivamente, escolhendo uma opção aleatória."""
    if not texto:
        return texto
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = _SPINTAX_RE.sub(lambda m: random.choice(m.group(1).split('|')), texto)
    return texto
