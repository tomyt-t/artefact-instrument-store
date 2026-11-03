"""Rules from the manual that are computed in code: installments, shipping, opening hours and store data."""
from datetime import UTC, datetime

import pytest

from emporio import policies
from emporio.policies import STORE, local_shipping_fee, max_installments, opening_status

TZ = policies.TIMEZONE


@pytest.mark.parametrize('amount, n', [
    (0, 1), (99.99, 1), (100, 2), (149.99, 2), (150, 3), (319.99, 3), (320, 4), (399.99, 4), (400, 5),
    (479.99, 5), (480, 6), (699.99, 6), (700, 7), (899.99, 8), (900, 9), (1199.99, 11), (1200, 12), (18900, 12),
])
def test_max_installments_follows_minimum_installment_tiers(amount, n):
    # section 3.1: R$ 50 per installment up to 3x, R$ 80 from 4x to 6x, R$ 100 from 7x to 12x
    assert max_installments(amount) == n


def test_local_shipping_is_free_only_strictly_above_500():
    assert [local_shipping_fee(v) for v in (0, 499.99, 500, 500.01)] == [35.0, 35.0, 35.0, 0.0]


@pytest.mark.parametrize('now, expected', [
    (datetime(2026, 11, 7, 8, 59, tzinfo=TZ), (False, 'Fechado · abre hoje às 9h')),
    (datetime(2026, 11, 7, 9, 0, tzinfo=TZ), (True, 'Aberto agora · até 13h')),
    (datetime(2026, 11, 7, 12, 59, tzinfo=TZ), (True, 'Aberto agora · até 13h')),
    (datetime(2026, 11, 7, 13, 0, tzinfo=TZ), (False, 'Fechado · abre segunda às 9h')),
    (datetime(2026, 11, 8, 10, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),
    (datetime(2026, 11, 9, 8, 0, tzinfo=TZ), (False, 'Fechado · abre hoje às 9h')),
    (datetime(2026, 11, 9, 9, 0, tzinfo=TZ), (True, 'Aberto agora · até 18h')),
    (datetime(2026, 11, 6, 17, 59, 59, tzinfo=TZ), (True, 'Aberto agora · até 18h')),
    (datetime(2026, 11, 6, 18, 0, tzinfo=TZ), (False, 'Fechado · abre amanhã às 9h')),
], ids=str)
def test_opening_status_on_regular_days(now, expected):
    assert opening_status(now) == expected


def test_opening_status_uses_campo_grande_time():
    # Campo Grande is UTC-4 all year
    assert opening_status(datetime(2026, 11, 7, 16, 59, tzinfo=UTC)) == (True, 'Aberto agora · até 13h')
    assert opening_status(datetime(2026, 11, 7, 17, 0, tzinfo=UTC)) == (False, 'Fechado · abre segunda às 9h')


def test_store_data_and_constants():
    assert STORE.street == 'Rua 14 de Maio, 3200 — Centro'
    assert STORE.city == 'Campo Grande - MS, 79202-333'
    assert STORE.phone == '(67) 3341-4444'
    assert STORE.email == 'contato@emporiodamusica.com.br'
    assert policies.OPENING_HOURS[5] == (9, 13)
    assert 6 not in policies.OPENING_HOURS  # closed on Sundays
    assert policies.PIX_DISCOUNT == 0.05
    assert policies.LARGE_INSTRUMENT_CATEGORIES == {3}
