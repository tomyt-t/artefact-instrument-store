"""Deterministic checks for one turn of a conversation: no model grades the model.

Text is compared with accents, case, markdown and the space after 'R$' removed, so the checks judge what was said
and not how it was formatted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from emporio.agent.events import AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts
from emporio.data import normalize
from emporio.i18n import STORE_LANGUAGE
from emporio.policies import TIMEZONE, opening_status

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TIMEZONE)  # Saturday, open until 13h; Monday 02/11 is Finados
FALSE_PROMISES = ('te aviso', 'te avise', 'te avisar', 'encaminhei', 'registrei', 'reservei')  # no tool does any of these
WEEKDAY_NAMES = ('segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo')
WEEKDAY_AND_DATE = re.compile(rf'\b({"|".join(WEEKDAY_NAMES)})(?:-feira)?,? \(?(?:dia )?(\d{{1,2}})/(\d{{2}})(?:/(\d{{4}}))?(?! e )')


@dataclass(frozen=True)
class Turn:
    message: str
    has: tuple[str, ...] = ()  # every one of these must be in the answer
    any_of: tuple[tuple[str, ...], ...] = ()  # at least one of each group
    not_has: tuple[str, ...] = ()  # none of these
    tools: tuple[str, ...] = ()  # tools that must run in this turn
    not_tools: tuple[str, ...] = ()
    events: tuple[str, ...] = ()  # exact storefront actions, e.g. 'ShowOrder(7)'
    not_events: tuple[str, ...] = ()  # action prefixes that must not happen, e.g. 'AddToCart'


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    turns: tuple[Turn, ...]
    now: datetime = NOW
    cart: tuple[tuple[int, int], ...] = ()  # (product id, quantity) already in the cart
    viewing: int | None = None  # product page open while chatting
    core: bool = False  # part of the short battery
    language: str = STORE_LANGUAGE  # of the page; the general checks only understand Portuguese


def clean(text: str) -> str:
    text = normalize(text).replace('*', '').replace('`', '').replace(' ', ' ').replace('r$ ', 'r$')
    # an English answer may write r$1,299.00: the same value as r$1.299,00, which is what the checks compare
    text = re.sub(r'r\$(\d{1,3}(?:,\d{3})*)\.(\d{2})\b', lambda m: f'r${m[1].replace(",", ".")},{m[2]}', text)
    return ' '.join(text.split())


def describe(event) -> str | None:
    """A storefront action in the form the scenarios use; None for anything else."""
    match event:
        case ShowProducts():
            return f'ShowProducts({",".join(map(str, sorted(event.product_ids)))})'
        case OpenProduct():
            return f'OpenProduct({event.product_id})'
        case AddToCart():
            return f'AddToCart({event.product_id},{event.quantity})'
        case RemoveFromCart():
            return f'RemoveFromCart({event.product_id})'
        case OpenCart():
            return 'OpenCart()'
        case ShowOrder():
            return f'ShowOrder({event.order_id})'
    return None


def money(text: str) -> set[float]:
    """Every value in reais written in the text."""
    found = re.findall(r'r\$(\d[\d.]*(?:,\d{1,2})?)', clean(text))
    return {float(value.rstrip('.').replace('.', '').replace(',', '.')) for value in found}


def invented_values(answer: str, sources: str) -> list[float]:
    """Values in the answer with no origin: not in the tool results or in what the customer said, nor the sum of two
    of those (the PIX total plus shipping is a fair sum). This catches inventions no scenario could foresee."""
    known = money(sources)
    known |= {round(a + b, 2) for a in known for b in known}
    return sorted(value for value in money(answer) if value not in known)


def wrong_weekdays(answer: str) -> list[str]:
    """A weekday and a date that do not match, like 'terça-feira (02/11)' when 02/11 is a Monday."""
    wrong = []
    for name, day, month, year in WEEKDAY_AND_DATE.findall(clean(answer)):
        try:
            actual = WEEKDAY_NAMES[date(int(year or NOW.year), int(month), int(day)).weekday()]
        except ValueError:
            actual = None
        if actual != name:
            wrong.append(f'{name} {day}/{month}')
    return wrong


def wrong_addresses(answer: str) -> list[str]:
    """Any street other than the store's: the customer is told to go there."""
    return [street for street in re.findall(r'\brua [^,.;!?]{1,30}', clean(answer))
            if not street.startswith('rua 14 de maio')]


def wrong_opening(answer: str, now: datetime) -> str | None:
    """Saying the store is closed right now while it is open, or the other way round."""
    text, is_open = clean(answer), opening_status(now)[0]
    said = (r'(?:estamos fechad\w*|loja esta fechada)(?= agora| no momento| neste momento|[.,!(]| \()'
            r'|(?:agora|neste momento|no momento)[^.!?]{0,20}fechad' if is_open
            else r'(?<!nao )estamos abert\w* (?:agora|hoje)|(?:agora|neste momento|no momento)[^.!?]{0,20}abert')
    return found[0] if (found := re.search(said, text)) else None


def mentions(item: str, text: str) -> bool:
    """The item at the start of a word: inside 'combinação', 'nao' is not a refusal."""
    return re.search(rf'(?<![a-z0-9]){re.escape(clean(item))}', text) is not None


def check_turn(turn: Turn, answer: str, tools: list[str], events: list[str], sources: str | None = None,
               now: datetime | None = None) -> list[str]:
    """What this turn got wrong; empty when it passes. `sources` is the text the model had to rely on, `now` the
    moment of the conversation."""
    text = clean(answer)
    failures = [f'faltou "{item}"' for item in turn.has if not mentions(item, text)]
    failures += [f'faltou um de {list(group)}' for group in turn.any_of if not any(mentions(i, text) for i in group)]
    failures += [f'não podia dizer "{item}"' for item in turn.not_has if clean(item) in text]
    failures += [f'não chamou {tool}' for tool in turn.tools if tool not in tools]
    failures += [f'não podia chamar {tool}' for tool in turn.not_tools if tool in tools]
    failures += [f'faltou a ação {event}' for event in turn.events if event not in events]
    failures += [f'não podia acontecer {event}' for event in events if event.startswith(turn.not_events or ('\0',))]
    if not text or text.endswith((',', ':', ';', ' de', ' e', ' a', ' o', ' na', ' no', ' em', ' com', ' para')):
        failures.append('resposta vazia ou cortada')
    failures += [f'dia da semana errado: {item}' for item in wrong_weekdays(answer)]
    failures += [f'endereço errado: {item}' for item in wrong_addresses(answer)]
    if now and (said := wrong_opening(answer, now)):
        failures.append(f'horário errado: "{said}"')
    failures += [f'prometeu o que não faz: "{item}"' for item in FALSE_PROMISES if item in text]
    if 'ferramenta' in text:
        failures.append('falou em ferramentas com o cliente')
    if sources is not None:
        failures += [f'valor sem origem: R$ {value:.2f}' for value in invented_values(answer, sources)]
    return failures
