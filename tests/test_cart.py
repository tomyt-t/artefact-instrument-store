"""Cart totals and stock rules shared by the storefront and the agent."""
import pytest

from emporio.cart import Cart
from emporio.policies import max_installments


def test_add_rejects_unknown_and_unavailable_products(store):
    cart = Cart(store, {})
    assert [cart.add(pid) for pid in (999, 96, 113, 130, 136)] == [
        'Produto não encontrado.',
        'Giannini GF-3D Dreadnought Sunburst está indisponível (esgotado).',
        'Shelby SN-7C 7 Cordas Nylon Natural está indisponível (fora de linha).',
        'Kala KA-ABP-CTG Barítono Cedar está indisponível (em breve).',
        'Ibanez Bass 2X Electric Bass está indisponível (fora de linha).',
    ]
    assert cart.count == 0


def test_add_respects_stock(store):
    storage = {}
    cart = Cart(store, storage)
    assert cart.add(93) is None
    assert cart.add(93) is None
    assert cart.add(93) == 'Só temos 2 unidade(s) de Martin D-28 Dreadnought Natural em estoque.'
    assert storage == {'93': 2}
    assert Cart(store, {}).add(108, 2) == 'Só temos 1 unidade(s) de Martin D-28E Elétrico Aço Natural em estoque.'


def test_totals_with_promotion_and_pix(store):
    cart = Cart(store, {})
    cart.add(121)  # Ohana CK-20, 20% off
    cart.add(81)
    assert (cart.count, cart.list_total, cart.total, cart.promo_savings, cart.pix_total) == (
        2, 1148.9, 1039.1, 109.8, 1009.1)
    assert (cart.missing_for_free_shipping, cart.needs_freight_quote) == (0.0, False)


def test_promotion_with_quantity(store):
    cart = Cart(store, {})
    cart.add(127, 3)
    assert (cart.total, cart.list_total, cart.promo_savings, cart.pix_total) == (969.3, 1077.0, 107.7, 969.3)


def test_free_shipping_gap_and_installments(store):
    cart = Cart(store, {})
    cart.add(119, 2)
    assert (cart.total, cart.pix_total, cart.missing_for_free_shipping) == (319.8, 303.82, 180.2)
    assert max_installments(cart.total) == 3  # 4x would be R$ 79,95, below the R$ 80 minimum


def test_drum_kit_needs_a_freight_quote(store):
    cart = Cart(store, {})
    cart.add(139)
    assert cart.needs_freight_quote


def test_set_quantity_clamps_to_stock_and_removes(store):
    storage = {}
    cart = Cart(store, storage)
    cart.set_quantity(93, 5)
    assert storage == {'93': 2}
    cart.set_quantity(93, 0)
    assert storage == {}
    with pytest.raises(KeyError):
        cart.set_quantity(999, 1)


def test_lines_ignore_products_no_longer_in_the_catalog(store):
    cart = Cart(store, {'81': 1, '9999': 3})
    assert [(line.product.id, line.quantity) for line in cart.lines] == [(81, 1)]
    assert cart.count == 1


def test_snapshot_for_the_agent(store):
    cart = Cart(store, {})
    cart.add(121)
    cart.add(81)
    assert cart.snapshot() == {
        'items': [{'product_id': 121, 'name': 'Ohana CK-20 Concert Natural', 'quantity': 1, 'unit_price': 439.2},
                  {'product_id': 81, 'name': 'Yamaha C40 Nylon Natural', 'quantity': 1, 'unit_price': 599.9}],
        'total': 1039.1,
        'pix_total': 1009.1,
    }


def test_listeners_only_run_on_changes(store):
    calls = []
    cart = Cart(store, {})
    cart.on_change(lambda: calls.append(1))
    cart.add(999)
    cart.add(96)
    assert calls == []
    cart.add(81)
    cart.remove(81)
    assert len(calls) == 2
