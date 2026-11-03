"""Holidays in the opening hours, and the exchange, return and warranty deadlines (sections 2, 3.1, 4 and 8)."""
from datetime import date, datetime

import pytest

from emporio import policies
from emporio.policies import (
    can_split_payment,
    delivery_days_late,
    easter,
    is_holiday,
    next_opening,
    opening_status,
    return_windows,
)

TZ = policies.TIMEZONE


@pytest.mark.parametrize('now, expected', [
    (datetime(2026, 10, 31, 13, 0, tzinfo=TZ), (False, 'Fechado · abre terça às 9h')),  # Monday is Finados
    (datetime(2026, 11, 2, 10, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),
    (datetime(2026, 11, 19, 19, 0, tzinfo=TZ), (False, 'Fechado · abre sábado às 9h')),  # Friday 20/11
    (datetime(2026, 11, 20, 10, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),
    (datetime(2026, 12, 24, 19, 0, tzinfo=TZ), (False, 'Fechado · abre sábado às 9h')),  # Christmas on Friday
    (datetime(2026, 12, 25, 10, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),
    (datetime(2026, 4, 3, 10, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),  # Good Friday
    (datetime(2026, 10, 10, 12, 0, tzinfo=TZ), (True, 'Aberto agora · até 13h')),  # Saturday before 11/10
], ids=str)
def test_opening_status_closes_on_holidays(now, expected):
    assert opening_status(now) == expected


def test_holiday_calendar():
    assert [easter(y) for y in (2026, 2027, 2028)] == [date(2026, 4, 5), date(2027, 3, 28), date(2028, 4, 16)]
    assert all(is_holiday(d) for d in (date(2026, 10, 11), date(2026, 6, 13), date(2026, 8, 26), date(2027, 3, 26)))
    # optional holidays ('pontos facultativos'): Carnaval and Corpus Christi
    assert not any(is_holiday(d) for d in (date(2026, 2, 16), date(2026, 2, 17), date(2026, 6, 4)))


def test_naive_datetimes_are_campo_grande_time():
    assert opening_status(datetime(2026, 11, 7, 12, 59)) == (True, 'Aberto agora · até 13h')
    assert opening_status(datetime(2026, 11, 7, 13, 0)) == (False, 'Fechado · abre segunda às 9h')


def test_online_purchase_deadlines():
    windows = return_windows(date(2026, 2, 10), date(2026, 2, 17), online=True)
    assert [(w.section, w.last_day) for w in windows] == [
        ('4.1', date(2026, 2, 24)), ('4.2', date(2026, 3, 12)), ('4.3', date(2026, 2, 24)), ('8.1', date(2026, 5, 18))]
    assert [w.is_open(date(2026, 2, 24)) for w in windows] == [True, True, True, True]
    assert [w.is_open(date(2026, 2, 25)) for w in windows] == [False, True, False, True]
    assert not any(w.is_open(date(2026, 10, 31)) for w in windows)


def test_in_store_purchase_has_no_right_of_withdrawal():
    assert [w.section for w in return_windows(date(2026, 10, 28), date(2026, 10, 28), online=False)] == [
        '4.2', '4.3', '8.1']


def test_deadlines_from_receipt_wait_for_the_delivery():
    windows = {w.section: w for w in return_windows(date(2026, 3, 1), None, online=True)}
    assert (windows['4.1'].last_day, windows['4.3'].last_day, windows['8.1'].last_day) == (None, None, None)
    assert not windows['4.1'].is_open(date(2026, 3, 2))
    assert windows['4.2'].last_day == date(2026, 3, 31)


def test_delivery_delay():
    assert delivery_days_late(date(2026, 2, 28), date(2026, 10, 31)) == 245
    assert delivery_days_late(date(2026, 11, 5), date(2026, 10, 31)) == 0
    assert delivery_days_late(None, date(2026, 10, 31)) == 0


def test_split_payment_only_above_2000():
    assert [can_split_payment(v) for v in (1999.99, 2000, 2000.01)] == [False, False, True]


def test_next_opening_skips_sundays_and_holidays():
    assert next_opening(datetime(2026, 10, 31, 12, 0, tzinfo=TZ)) == datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    assert next_opening(datetime(2026, 11, 3, 7, 30, tzinfo=TZ)) == datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    assert next_opening(datetime(2026, 11, 3, 10, 0, tzinfo=TZ)) == datetime(2026, 11, 4, 9, 0, tzinfo=TZ)
