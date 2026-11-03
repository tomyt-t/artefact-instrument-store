"""Business rules from data/políticas_da_loja.pdf that the code must compute (prices, shipping, hours).

Free-text policies (returns, warranty, privacy) stay in the PDF and are read by the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class StoreInfo:
    name: str = 'Empório da Música'
    slogan: str = 'Sua música começa aqui.'
    street: str = 'Rua 14 de Maio, 3200 — Centro'
    city: str = 'Campo Grande - MS, 79202-333'
    phone: str = '(67) 3341-4444'  # The manual lists (67) 3321-4500 as WhatsApp in section 7; we follow the company data table (1.2).
    email: str = 'contato@emporiodamusica.com.br'
    founded: int = 2008


STORE = StoreInfo()
TIMEZONE = ZoneInfo('America/Campo_Grande')

# weekday() -> (opens, closes); Sunday is closed (section 2)
OPENING_HOURS = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 13)}
WEEKDAYS = ('segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo')

PIX_DISCOUNT = 0.05  # section 3, not cumulative with promotions (6.2)
FREE_SHIPPING_ABOVE = 500.0  # Campo Grande metro area only (5.1)
LOCAL_SHIPPING_FEE = 35.0
LARGE_INSTRUMENT_CATEGORIES = {3}  # drum kits may need a special freight quote (5.2)
RETURN_WINDOW_DAYS = 7


def max_installments(amount: float) -> int:
    """Interest-free installments allowed by the minimum installment of each tier (section 3.1)."""
    for n in range(12, 1, -1):
        minimum = 100 if n >= 7 else 80 if n >= 4 else 50
        if amount / n >= minimum:
            return n
    return 1


def local_shipping_fee(total: float) -> float:
    return 0.0 if total > FREE_SHIPPING_ABOVE else LOCAL_SHIPPING_FEE


def _in_campo_grande(moment: datetime) -> datetime:
    """Naive datetimes are Campo Grande time, not the server's local time."""
    return moment.replace(tzinfo=TIMEZONE) if moment.tzinfo is None else moment.astimezone(TIMEZONE)


def hours_on(day: date) -> tuple[int, int] | None:
    """Opening and closing hour on that day, or None when the store is closed (Sundays)."""
    return OPENING_HOURS.get(day.weekday())


def opening_status(now: datetime | None = None) -> tuple[bool, str]:
    """Whether the store is open right now, and a short human-readable status."""
    now = _in_campo_grande(now or datetime.now(TIMEZONE))
    hours = hours_on(now.date())
    if hours and hours[0] <= now.hour < hours[1]:
        return True, f'Aberto agora · até {hours[1]}h'
    if opening := next_opening(now):
        days_ahead = (opening.date() - now.date()).days
        when = 'hoje' if days_ahead == 0 else 'amanhã' if days_ahead == 1 else WEEKDAYS[opening.weekday()]
        return False, f'Fechado · abre {when} às {opening.hour}h'
    return False, 'Fechado'


def next_opening(now: datetime) -> datetime | None:
    """When the store opens next, after `now`, skipping Sundays."""
    now = _in_campo_grande(now)
    for days_ahead in range(0, 8):
        day = now + timedelta(days=days_ahead)
        hours = hours_on(day.date())
        if hours and (days_ahead > 0 or now.hour < hours[0]):
            return day.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return None
