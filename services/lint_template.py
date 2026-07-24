"""
Lint de templates para mensagens de primeiro contato — PRD Antiban Fase 5.
Mensagens para contatos sem histórico não podem conter URL nem palavras-gatilho
(reduz o risco de a mensagem ser lida como spam/comercial por quem nunca
recebeu nada da empresa antes).
"""
import re

_URL_RE = re.compile(r'https?://|www\.', re.IGNORECASE)


def validar_template_primeiro_contato(texto: str, palavras_gatilho: list) -> list:
    """Retorna lista de problemas encontrados (vazia = ok)."""
    problemas = []
    texto = texto or ''
    if _URL_RE.search(texto):
        problemas.append('Contém URL — não permitido em mensagem de primeiro contato.')
    texto_low = texto.lower()
    for p in palavras_gatilho:
        p = p.strip()
        if p and p.lower() in texto_low:
            problemas.append(f'Contém palavra-gatilho: "{p}"')
    return problemas
