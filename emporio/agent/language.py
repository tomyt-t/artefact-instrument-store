"""Which language to answer in, decided in code: left to itself, the model drifts to the language of the tool data."""
from __future__ import annotations

import re

WORDS = {
    'pt-BR': frozenset("""a as com como custa da de do e ela ele em esta eu meu minha nao no nos o obrigado oi os para pedido
                          por posso quais qual quando quanto que quero sim tem uma um voce voces vou""".split()),
    'en': frozenset("""a and are can cost do does for have hello hi how i in is it much my of on order please thanks the to
                       up want what when which with you your""".split()),
    'fr': frozenset("""a avec avez bonjour combien commande des du est et je la le les ma merci moi mon nous ou pour puis
                       quel quelle quelles quels que une vous""".split()),
}
ANSWER_IN = {'pt-BR': 'Responda em português do Brasil.', 'en': 'Answer in English.', 'fr': 'Réponds en français.'}


def answer_language(message: str, page_language: str) -> str:
    """The language most words of the message belong to; the page's when the message doesn't tell ('ok', 'Yamaha C40')."""
    accents = str.maketrans('áàâãéêíóôõúüç', 'aaaaeeiooouuc')
    words = re.findall(r"[a-z]+", message.lower().translate(accents))
    scores = {language: sum(word in known for word in words) for language, known in WORDS.items()}
    best = max(scores, key=scores.get)
    tied = sum(score == scores[best] for score in scores.values()) > 1
    return page_language if tied or not scores[best] else best
