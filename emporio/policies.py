"""Business rules from data/políticas_da_loja.pdf that the code must compute: prices, shipping, hours, holidays,
deadlines and split payment.

The wording of the policies stays in the PDF and is read by the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class StoreInfo:
    name: str = 'Empório da Música'
    street: str = 'Rua 14 de Maio, 3200 — Centro'
    city: str = 'Campo Grande - MS, 79202-333'
    phone: str = '(67) 3341-4444'  # The manual lists (67) 3321-4500 as WhatsApp in section 7; we follow the company data table (1.2).
    email: str = 'contato@emporiodamusica.com.br'
    founded: int = 2008


STORE = StoreInfo()
TIMEZONE = ZoneInfo('America/Campo_Grande')

# weekday() -> (opens, closes); Sunday and holidays are closed (section 2)
OPENING_HOURS = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 13)}
WEEKDAYS = ('segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo')
# (month, day): national holidays, Mato Grosso do Sul (11/10) and Campo Grande (13/06, 26/08), plus Good Friday.
# Optional holidays ('pontos facultativos') such as Carnaval and Corpus Christi do not close the store.
HOLIDAYS = {(1, 1), (4, 21), (5, 1), (6, 13), (8, 26), (9, 7), (10, 11), (10, 12), (11, 2), (11, 15), (11, 20),
            (12, 25)}

PIX_DISCOUNT = 0.05  # section 3, not cumulative with promotions (6.2)
SPLIT_PAYMENT_ABOVE = 2000.0  # combining payment methods (e.g. PIX + card) in one purchase (3.1)
FREE_SHIPPING_ABOVE = 500.0  # Campo Grande metro area only (5.1)
LOCAL_SHIPPING_FEE = 35.0
LARGE_INSTRUMENT_CATEGORIES = {3}  # drum kits may need a special freight quote (5.2)
RETURN_WINDOW_DAYS = 7
# Deadlines in calendar days: RETURN_WINDOW_DAYS for withdrawal ('arrependimento', 4.1) and preference exchange
# ('troca por preferência', 4.3), counted from receipt; defect exchange (4.2) from the purchase; legal warranty (8.1)
# from receipt.
DEFECT_EXCHANGE_DAYS = 30
LEGAL_WARRANTY_DAYS = 90


@dataclass(frozen=True)
class ReturnWindow:
    """One right from sections 4 and 8 and the last calendar day to use it."""
    section: str
    right: str
    last_day: date | None  # None while the product has not been received

    def is_open(self, today: date) -> bool:
        return self.last_day is not None and today <= self.last_day


def max_installments(amount: float) -> int:
    """Interest-free installments allowed by the minimum installment of each tier (section 3.1)."""
    for n in range(12, 1, -1):
        minimum = 100 if n >= 7 else 80 if n >= 4 else 50
        if amount / n >= minimum:
            return n
    return 1


def can_split_payment(total: float) -> bool:
    """`total` is the total with promotions (the card price), the same base as the shipping rule."""
    return total > SPLIT_PAYMENT_ABOVE


def local_shipping_fee(total: float) -> float:
    return 0.0 if total > FREE_SHIPPING_ABOVE else LOCAL_SHIPPING_FEE


def _in_campo_grande(moment: datetime) -> datetime:
    """Naive datetimes are Campo Grande time, not the server's local time."""
    return moment.replace(tzinfo=TIMEZONE) if moment.tzinfo is None else moment.astimezone(TIMEZONE)


def easter(year: int) -> date:
    """Easter Sunday (Meeus/Jones/Butcher algorithm); Good Friday is two days before."""
    a, b, c = year % 19, year // 100, year % 100
    h = (19 * a + b - b // 4 - (b - (b + 8) // 25 + 1) // 3 + 15) % 30
    shift = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7
    m = (a + 11 * h + 22 * shift) // 451
    month, day = divmod(h + shift - 7 * m + 114, 31)
    return date(year, month, day + 1)


def is_holiday(day: date) -> bool:
    return (day.month, day.day) in HOLIDAYS or day == easter(day.year) - timedelta(days=2)


def hours_on(day: date) -> tuple[int, int] | None:
    """Opening and closing hour on that day, or None when the store is closed (Sundays and holidays)."""
    return None if is_holiday(day) else OPENING_HOURS.get(day.weekday())


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
    """When the store opens next, after `now`, skipping Sundays and holidays."""
    now = _in_campo_grande(now)
    for days_ahead in range(0, 8):
        day = now + timedelta(days=days_ahead)
        hours = hours_on(day.date())
        if hours and (days_ahead > 0 or now.hour < hours[0]):
            return day.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return None


def return_windows(purchased: date, received: date | None, *, online: bool) -> list[ReturnWindow]:
    """Exchange, return and warranty deadlines in calendar days (4.1, 4.2, 4.3 and 8.1).

    The manual does not say from when the preference exchange (4.3) counts; we use the receipt, like 4.1.
    The right of withdrawal (4.1) only applies to online purchases.
    """
    def after_receipt(days: int) -> date | None:
        return received + timedelta(days=days) if received else None

    windows = [ReturnWindow('4.1', 'Direito de arrependimento', after_receipt(RETURN_WINDOW_DAYS))] if online else []
    return windows + [
        ReturnWindow('4.2', 'Troca por defeito de fabricação', purchased + timedelta(days=DEFECT_EXCHANGE_DAYS)),
        ReturnWindow('4.3', 'Troca por preferência', after_receipt(RETURN_WINDOW_DAYS)),
        ReturnWindow('8.1', 'Garantia legal', after_receipt(LEGAL_WARRANTY_DAYS)),
    ]


def delivery_days_late(estimated: date | None, today: date) -> int:
    """Days past the estimated delivery date (0 when there is no estimate or it has not passed)."""
    return max(0, (today - estimated).days) if estimated else 0
