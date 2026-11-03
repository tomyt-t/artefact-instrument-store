"""Loading and treatment of the store CSVs."""
from datetime import date

from emporio.data import load_store, normalize


def test_loads_every_table(store):
    assert (len(store.products), len(store.customers), len(store.orders), len(store.categories)) == (65, 50, 20, 9)
    assert [c.id for c in store.categories_in_use] == [1, 2, 3, 4, 5, 9]
    assert load_store() is store


def test_only_active_promotions_are_loaded(store):
    assert {pid for pid, p in store.products.items() if p.promotion} == {90, 94, 121, 127}
    assert store.products[127].promotion.discount_percent == 10


def test_inactive_promotion_never_changes_price(store):
    gd20 = store.products[95]
    assert gd20.promotion is None
    assert (gd20.price, gd20.pix_price) == (2199.0, 2089.05)


def test_pix_discount_is_not_cumulative_with_promotions(store):
    assert [(store.products[i].price, store.products[i].pix_price) for i in (121, 90, 127)] == [
        (439.2, 439.2), (1967.18, 1967.18), (323.1, 323.1)]
    assert store.products[94].price == 5519.08


def test_pix_is_5_percent_off_the_list_price(store):
    assert [store.products[i].pix_price for i in (92, 108, 119)] == [1234.05, 11400.0, 151.91]


def test_availability(store):
    labels = [store.products[i].availability_label for i in (96, 113, 130, 108, 93, 90, 136)]
    assert labels == ['Esgotado', 'Fora de linha', 'Em breve', 'Últimas unidades', 'Últimas unidades', 'Em estoque',
                      'Fora de linha']
    # status wins over stock: 136 is discontinued with 13 units, 130 is coming soon with 2
    assert [store.products[i].is_available for i in (136, 130, 96, 81)] == [False, False, False, True]


def test_brand_and_image(store):
    assert [store.products[i].brand for i in (118, 116, 135, 140, 133)] == ['Kalani', 'Kala', 'Music Man', 'Pearl', 'PRS']
    assert [store.products[i].image for i in (95, 94, 111, 86, 81, 131, 139, 127)] == [
        'violao-dreadnought', 'violao-eletroacustico', 'violao-7-cordas', 'violao-folk-aco', 'violao-classico-nylon',
        'guitarra-eletrica', 'bateria-acustica', 'ukulele']


def test_structured_search(store):
    assert [p.id for p in store.search(category_id=5, max_price=1000, only_available=True)] == [
        81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 107, 112]
    assert [p.id for p in store.search(category_id=5, max_price=1000)] == [
        81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 96, 107, 112, 113]
    assert store.search(category_id=6) == []


def test_search_max_price_uses_the_promotional_price(store):
    assert 121 in [p.id for p in store.search(category_id=9, max_price=439.2)]
    assert 121 not in [p.id for p in store.search(category_id=9, max_price=439.19)]


def test_order_is_only_found_with_the_owner_email(store):
    assert store.find_order(8, 'anacarol.ferreira@coldmail.com').id == 8
    assert store.find_order(8, '  AnaCarol.Ferreira@COLDMAIL.com ').id == 8
    assert store.find_order(8, 'lucas.mendes@jmail.com') is None  # another customer's e-mail
    assert store.find_order(8, '') is None
    assert store.find_order(999, 'anacarol.ferreira@coldmail.com') is None


def test_order_fields(store):
    assert [store.orders[i].payment_label for i in (1, 2, 4, 8)] == [
        'PIX', 'Cartão de crédito em 6x', 'Boleto bancário', 'Cartão de débito']
    assert [store.orders[i].status_label for i in (1, 8, 9, 13, 17)] == [
        'Entregue', 'Enviado', 'Pagamento confirmado', 'Aguardando pagamento', 'Cancelado']
    assert store.orders[15].items == ((82, 1), (106, 1))
    assert store.orders[8].estimated_delivery == date(2026, 2, 28)
    assert (store.orders[9].estimated_delivery, store.orders[9].tracking_code) == (None, '')


def test_references_between_tables(store):
    for order in store.orders.values():
        assert order.customer_id in store.customers
        assert order.items, f'order {order.id} has no items'
        assert all(pid in store.products for pid, _ in order.items)
    assert all(p.category_id in store.categories for p in store.products.values())


def test_normalize_and_first_name(store):
    assert normalize('Violão AÇO Élétrico ç') == 'violao aco eletrico c'
    assert store.customers[3].first_name == 'Pedro'
