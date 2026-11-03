"""Agent tools: what the model reads back and which storefront events each call produces."""
import json
from datetime import datetime

import pytest

from emporio.agent.base import AgentContext
from emporio.agent.events import AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts
from emporio.agent.tools import MAX_FAILED_LOOKUPS, MAX_LISTED, ORDER_NOT_FOUND, OrderGuard, Toolbox
from emporio.cart import Cart
from emporio.policies import TIMEZONE

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TIMEZONE)  # Saturday, open until 13h; Monday 02/11 is Finados
ANA = 'anacarol.ferreira@coldmail.com'  # order 8: shipped, 245 days past the estimated delivery


@pytest.fixture
def guard():
    return OrderGuard()


@pytest.fixture
def make(store, guard):
    def make(*, cart=None, now=NOW, verified=(), session='s1', viewing=None, seen=None):
        context = AgentContext(session_id=session, cart=cart or {}, now=now, verified_orders=set(verified),
                               viewing_product_id=viewing)
        toolbox = Toolbox(store, context, guard)
        toolbox.seen_ids.update(store.products if seen is None else seen)  # by default, as if the model had searched
        return toolbox
    return make


@pytest.fixture
def tools(make):
    return make()


def ids(products):
    return [p['id'] for p in products]


def test_schemas_are_valid_function_declarations(tools):
    schemas = tools.schemas()
    assert [s['function']['name'] for s in schemas] == [
        'buscar_produtos', 'ver_produto', 'ver_carrinho', 'adicionar_ao_carrinho', 'remover_do_carrinho',
        'consultar_pedido']
    for schema in schemas:
        parameters = schema['function']['parameters']
        assert parameters['type'] == 'object'
        assert set(parameters.get('required', [])) <= set(parameters['properties'])
    json.dumps(schemas)


# --- catalog -------------------------------------------------------------------------------------------------------

def test_search_describes_all_the_answers_to_the_challenges_first_question(tools):
    result = tools.call('buscar_produtos', {'categoria': 'Violões', 'preco_maximo': 1000})
    assert result.events == (ShowProducts((81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 107, 112), 'Violões até R$ 1.000,00'),)
    assert result.data['encontrados'] == 12 and len(result.data['produtos']) == 12 and 'observacao' not in result.data
    assert result.data['produtos'][0] == {
        'id': 81, 'nome': 'Yamaha C40 Nylon Natural', 'categoria': 'Violões', 'disponibilidade': 'Em estoque',
        'preco': 'R$ 599,90', 'preco_pix': 'R$ 569,90', 'parcelas': 'até 6x de R$ 99,98 sem juros',
        'frete_em_campo_grande': 'grátis',
        'especificacoes': {'tampo': 'Spruce', 'fundo e laterais': 'Meranti', 'braço': 'Nato', 'cordas': 'nylon',
                           'escala': '650mm', 'captação (eletroacústico)': 'não', 'cor': 'Natural'}}


def test_search_gives_the_local_shipping_of_the_current_price(tools):
    found = tools.call('buscar_produtos', {'consulta': 'Ohana CK-20'}).data['produtos'][0]
    assert (found['preco'], found['preco_original'], found['frete_em_campo_grande']) == (
        'R$ 439,20', 'R$ 549,00', 'R$ 35,00')  # the list price alone would ship for free


def test_search_by_model_code_and_price_order(tools):
    assert ids(tools.call('buscar_produtos', {'consulta': 'gd-20'}).data['produtos']) == [95]
    prices = tools.call('buscar_produtos', {'categoria': 'Ukuleles', 'ordem': 'menor_preco'}).data['produtos']
    assert prices[0]['nome'].startswith('Shelby SU-21S')


def test_promotion_shows_the_original_price_and_no_extra_pix_discount(tools):
    [ohana] = tools.call('buscar_produtos', {'consulta': 'CK-20'}).data['produtos']
    assert (ohana['preco'], ohana['preco_original'], ohana['promocao'], ohana['preco_pix']) == (
        'R$ 439,20', 'R$ 549,00', 'Desconto Exclusivo: 20% de desconto', 'R$ 439,20')
    assert 'não se soma' in ohana['pix']  # asked later, the model said the price already had both discounts


def test_promotions_can_be_listed(tools):
    result = tools.call('buscar_produtos', {'em_promocao': True})
    assert ids(result.data['produtos']) == [90, 94, 121, 127] and result.events[0].title == 'Produtos em promoção'
    assert all('preco_original' in p and 'promocao' in p for p in result.data['produtos'])
    assert ids(tools.call('buscar_produtos', {'em_promocao': True, 'categoria': 'Violões'}).data['produtos']) == [
        90, 94]


def test_no_promotion_is_not_the_same_as_unavailable(tools):
    for arguments in ({'consulta': 'Yamaha C40', 'em_promocao': True}, {'categoria': 'Guitarras', 'em_promocao': True}):
        result = tools.call('buscar_produtos', arguments)
        assert result.data == {'encontrados': 0, 'produtos': []} and result.events == ()


def test_search_reports_unavailable_matches_when_nothing_is_available(tools):
    result = tools.call('buscar_produtos', {'consulta': 'GF-3D'})
    assert result.data['encontrados'] == 0 and result.events == ()
    assert [(p['id'], p['disponibilidade']) for p in result.data['indisponiveis']] == [(96, 'Esgotado')]
    assert ids(result.data['alternativas']) == [91, 92, 95]
    included = tools.call('buscar_produtos', {'consulta': 'SN-7C', 'so_disponiveis': False})  # only a discontinued one
    assert ids(included.data['produtos']) == [113] and ids(included.data['alternativas']) == [110, 109, 111]


def test_accessories_are_out_of_scope(tools):
    amplifier = tools.call('buscar_produtos', {'consulta': 'amplificador'})
    assert amplifier.data['encontrados'] == 0 and 'escopo' in amplifier.data
    strings = tools.call('buscar_produtos', {'consulta': 'cordas'})  # steel- and nylon-string guitars match
    assert 'escopo' in strings.data and strings.events == ()  # nothing lights up until the customer says which
    steel = tools.call('buscar_produtos', {'categoria': 'Violões', 'consulta': 'cordas de aço'})
    assert steel.data['encontrados'] == 18 and steel.events  # 19 steel-string guitars, one sold out
    seven = tools.call('buscar_produtos', {'consulta': '7 cordas'})  # a kind of guitar, not strings
    assert seven.data['encontrados'] == 4 and 'escopo' not in seven.data and seven.events
    tuners = tools.call('buscar_produtos', {'consulta': 'afinadores?'})
    assert 'escopo' in tuners.data and tuners.events == ()


def test_a_query_without_words_is_not_the_whole_catalog(tools):
    for query in ('???', 'de', 'com de', '  -  '):
        result = tools.call('buscar_produtos', {'consulta': query})
        assert 'erro' in result.data and result.events == (), query
    assert tools.call('buscar_produtos', {'consulta': '???', 'categoria': 'Baixos'}).data['encontrados'] == 3


def test_an_instrument_with_a_feature_is_not_an_accessory(tools):
    for query in ('violão com pré-amplificador', 'violão com pré amplificador', 'violão com cordas de aço'):
        result = tools.call('buscar_produtos', {'consulta': query})
        assert 'escopo' not in result.data, query


def test_search_needs_a_valid_filter(tools):
    assert 'erro' in tools.call('buscar_produtos', {}).data
    assert 'Violões' in tools.call('buscar_produtos', {'categoria': 'Sopros'}).data['erro']
    assert 'erro' in tools.call('buscar_produtos', {'preco_maximo': '1.000'}).data  # ambiguous, never guessed
    assert tools.call('buscar_produtos', {'categoria': 'violões', 'preco_maximo': '1000'}).data['encontrados'] == 12


@pytest.mark.parametrize('value', [81, 81.0, '81', '#81', 'nº 81'])
def test_product_ids_from_the_model_are_coerced(tools, value):
    assert tools.call('ver_produto', {'produto_id': value}).events == (OpenProduct(81),)


@pytest.mark.parametrize('value', [81.5, True, 'C40', [81], -1, 999])
def test_bad_product_ids_are_reported_not_raised(tools, value):
    result = tools.call('ver_produto', {'produto_id': value})
    assert 'erro' in result.data and result.events == ()


def test_product_details_come_from_the_policies(tools):
    gd20 = tools.call('ver_produto', {'produto_id': 95}).data
    assert (gd20['preco_pix'], gd20['parcelas'], gd20['frete_em_campo_grande'], gd20['estoque']) == (
        'R$ 2.089,05', 'até 12x de R$ 183,25 sem juros', 'grátis', 5)
    soprano = tools.call('ver_produto', {'produto_id': 119}).data
    assert (soprano['parcelas'], soprano['frete_em_campo_grande']) == ('até 3x de R$ 53,30 sem juros', 'R$ 35,00')
    assert all(name in soprano['frete_outras_cidades'] for name in ('PAC', 'SEDEX', 'Jadlog', 'CEP', 'cidade vizinha'))
    assert '(67) 3341-4444' in tools.call('ver_produto', {'produto_id': 139}).data['frete_especial']


def test_unavailable_products_bring_available_alternatives(tools):
    sold_out = tools.call('ver_produto', {'produto_id': 96}).data  # a steel-string dreadnought, sold out
    assert sold_out['situacao'].startswith('Temporariamente sem estoque')
    assert ids(sold_out['alternativas']) == [91, 92, 95]  # available steel dreadnoughts, closest price first
    discontinued = tools.call('ver_produto', {'produto_id': 113}).data  # a 7-string guitar
    assert discontinued['situacao'] == 'Não faz mais parte do catálogo.'
    assert ids(discontinued['alternativas']) == [110, 109, 111]  # the other 7-string guitars
    coming = tools.call('ver_produto', {'produto_id': 130}).data
    assert coming['disponibilidade'] == 'Em breve' and 'estoque' not in coming  # the CSV still says 2


def test_conflicting_descriptions_are_flagged_in_searches_too(tools):
    [music_man] = tools.call('buscar_produtos', {'consulta': 'Music Man Bass 1X'}).data['produtos']
    assert 'confirmados com a equipe' in music_man['aviso'] and 'especificacoes' not in music_man


def test_conflicting_descriptions_are_withheld(tools):
    music_man = tools.call('ver_produto', {'produto_id': 135}).data
    assert 'descricao' not in music_man and 'especificacoes' not in music_man and 'Fender' not in str(music_man)
    assert 'confirmados com a equipe' in music_man['aviso']


# --- cart ----------------------------------------------------------------------------------------------------------

def test_add_checks_stock_against_the_customers_cart(make):
    tools = make(cart={'items': [{'product_id': 93, 'quantity': 2}]})
    assert tools.call('adicionar_ao_carrinho', {'produto_id': 93}).data == {
        'erro': 'Nada foi adicionado. Só temos 2 unidade(s) de Martin D-28 Dreadnought Natural em estoque. O cliente já tem todas as 2 no '
                'carrinho.'}
    assert 'já tem 2 no carrinho; cabem mais 10' in make(cart={'items': [{'product_id': 81, 'quantity': 2}]}).call(
        'adicionar_ao_carrinho', {'produto_id': 81, 'quantidade': 11}).data['erro']
    added = tools.call('adicionar_ao_carrinho', {'produto_id': 108})  # the last unit
    assert added.events == (AddToCart(108, 1),)
    assert added.data['carrinho']['total'] == 'R$ 34.998,00'
    second = tools.call('adicionar_ao_carrinho', {'produto_id': 108})
    assert 'Só temos 1 unidade(s)' in second.data['erro'] and second.events == ()


def test_add_refuses_an_id_the_model_did_not_look_up_this_turn(make):
    # the history has no ids: asked for a Yamaha C40, a model once added a guessed id, which was a Giannini
    guessed = make(seen=()).call('adicionar_ao_carrinho', {'produto_id': 101})
    assert 'buscar_produtos' in guessed.data['erro'] and guessed.events == ()
    searched = make(seen=())
    assert ids(searched.call('buscar_produtos', {'consulta': 'Yamaha C40'}).data['produtos']) == [81]
    assert searched.call('adicionar_ao_carrinho', {'produto_id': 81}).events == (AddToCart(81, 1),)
    opened = make(seen=())
    opened.call('ver_produto', {'produto_id': 95})
    assert opened.call('adicionar_ao_carrinho', {'produto_id': 95}).events == (AddToCart(95, 1),)
    alternative = make(seen=())
    alternative.call('ver_produto', {'produto_id': 96})
    assert alternative.call('adicionar_ao_carrinho', {'produto_id': 91}).events == (AddToCart(91, 1),)
    assert make(seen=(), viewing=108).call('adicionar_ao_carrinho', {'produto_id': 108}).events == (AddToCart(108, 1),)
    in_cart = make(seen=(), cart={'items': [{'product_id': 81, 'quantity': 1}]})
    assert in_cart.call('adicionar_ao_carrinho', {'produto_id': 81}).events == (AddToCart(81, 1),)


def test_an_unseen_id_says_nothing_was_added_and_which_product_it_is(make):
    tools = make(seen=())
    first = tools.call('adicionar_ao_carrinho', {'produto_id': 113})
    assert first.events == () and first.data['erro'].startswith('Nada foi adicionado')
    assert 'Shelby SN-7C' in first.data['erro'] and 'fora de linha' in first.data['erro'].lower()
    assert tools.call('adicionar_ao_carrinho', {'produto_id': 113}).events == ()  # still discontinued on the second try
    available = make(seen=())
    assert available.call('adicionar_ao_carrinho', {'produto_id': 81}).events == ()
    assert available.call('adicionar_ao_carrinho', {'produto_id': 81}).events == (AddToCart(81, 1),)


def test_only_listed_products_count_as_looked_up(make):
    tools = make(seen=())
    result = tools.call('buscar_produtos', {'preco_maximo': 1000000})
    listed = ids(result.data['produtos'])
    assert len(listed) == MAX_LISTED and tools.call('adicionar_ao_carrinho', {'produto_id': listed[0]}).events
    highlighted_only = result.events[0].product_ids[MAX_LISTED]
    assert 'buscar_produtos' in tools.call('adicionar_ao_carrinho', {'produto_id': highlighted_only}).data['erro']
    sold_out = make(seen=())
    sold_out.call('buscar_produtos', {'consulta': 'SN-7C'})
    assert 'fora de linha' in sold_out.call('adicionar_ao_carrinho', {'produto_id': 113}).data['erro']


def test_the_note_about_highlighted_products_is_true(tools):
    highlighted = tools.call('buscar_produtos', {'categoria': 'Violões'}).data['observacao']
    assert highlighted == 'Só os 15 primeiros estão listados. A vitrine destaca os 33.'
    assert tools.call('buscar_produtos', {'consulta': 'cordas'}).data['observacao'] == (
        'Só os 15 primeiros estão listados.')  # nothing lights up for a search for strings


@pytest.mark.parametrize('quantity', [0, -1, 1.5, 'dois'])
def test_add_refuses_bad_quantities(tools, quantity):
    result = tools.call('adicionar_ao_carrinho', {'produto_id': 81, 'quantidade': quantity})
    assert 'erro' in result.data and result.events == ()


def test_add_refuses_unavailable_products(tools):
    result = tools.call('adicionar_ao_carrinho', {'produto_id': 113})
    assert result.data == {'erro': 'Nada foi adicionado. Shelby SN-7C 7 Cordas Nylon Natural está indisponível (fora de linha).'}


def test_cart_rejects_ids_and_quantities_that_are_not_integers(store):
    storage = {}
    cart = Cart(store, storage)
    assert cart.add(81.0) == cart.add(True) == 'Produto não encontrado.'
    assert cart.add(81, 0) == cart.add(81, -2) == cart.add(81, 1.0) == (
        'A quantidade precisa ser um número inteiro de pelo menos 1.')
    assert storage == {}


def test_remove_and_view_cart(make):
    tools = make(cart={'items': [{'product_id': 121, 'quantity': 1}, {'product_id': 81, 'quantity': 1}]})
    cart = tools.call('ver_carrinho', {'mostrar': True})
    assert cart.events == (OpenCart(),)
    assert {key: cart.data[key] for key in ('total', 'economia_em_promocoes', 'total_no_pix', 'parcelas',
                                            'frete_em_campo_grande')} == {
        'total': 'R$ 1.039,10', 'economia_em_promocoes': 'R$ 109,80', 'total_no_pix': 'R$ 1.009,10',
        'parcelas': 'até 10x de R$ 103,91 sem juros', 'frete_em_campo_grande': 'grátis'}
    assert cart.data['pagamento_combinado'] == 'só em compras acima de R$ 2.000,00'
    promo, regular = cart.data['itens']
    assert (promo['preco_original'], promo['promocao']) == ('R$ 549,00', 'Desconto Exclusivo: 20% de desconto')
    assert 'preco_original' not in regular and 'promocao' not in regular
    removed = tools.call('remover_do_carrinho', {'produto_id': 81})
    assert removed.events == (RemoveFromCart(81),)
    assert removed.data['carrinho']['frete_em_campo_grande'] == 'R$ 35,00 (frete grátis acima de R$ 500,00)'
    assert 'não está no carrinho' in tools.call('remover_do_carrinho', {'produto_id': 81}).data['erro']


# --- orders --------------------------------------------------------------------------------------------------------

def test_order_needs_number_and_email(make):
    tools = make()
    asked = tools.call('consultar_pedido', {'numero_pedido': 8})
    assert 'e-mail' in asked.data['erro'] and asked.events == ()
    for email in ('pedro.oliveira@jmail.com', 'anacarol.ferreira@coldmail'):
        wrong = tools.call('consultar_pedido', {'numero_pedido': 8, 'email': email})
        assert wrong.data == {'erro': ORDER_NOT_FOUND} and wrong.events == ()
    assert tools.call('consultar_pedido', {'numero_pedido': 999, 'email': ANA}).data == {'erro': ORDER_NOT_FOUND}
    assert tools.context.verified_orders == set()


def test_verified_order_is_shown_as_recorded(make):
    tools = make()
    found = tools.call('consultar_pedido', {'numero_pedido': '#8', 'email': ' AnaCarol.Ferreira@Coldmail.com '})
    assert found.events == (ShowOrder(8),) and tools.context.verified_orders == {8}
    assert found.data == {
        'pedido': 8, 'cliente': 'Ana', 'feito_em': '15/02/2026', 'situacao': 'Enviado',
        'itens': [{'id': 120, 'nome': 'Kala KA-C Concert Mogno', 'quantidade': 1}], 'total_registrado': 'R$ 349,90',
        'pagamento': 'Cartão de débito', 'codigo_de_rastreio': 'BRJL5544332BR', 'previsao_de_entrega': '28/02/2026',
        'atraso': '245 dias depois da previsão de entrega, que era 28/02/2026',
        'o_que_fazer': 'Pedimos desculpas pela demora. Para resolver, fale com a equipe pelo (67) 3341-4444: o retorno é '
                       'em até 24 horas úteis (7.3). Quem avalia cancelamento e reembolso é a equipe.'}
    assert tools.call('consultar_pedido', {'numero_pedido': 8}).events == (ShowOrder(8),)  # no e-mail needed again


def test_stale_and_cancelled_orders(make):
    tools = make(verified=(10, 13, 19))
    stale = tools.call('consultar_pedido', {'numero_pedido': 13}).data
    assert stale['dias_desde_o_pedido'] == 233 and '24 horas úteis' in stale['o_que_fazer']
    assert tools.call('consultar_pedido', {'numero_pedido': 10}).data['total_registrado'] == 'R$ 8.732,00'
    assert tools.call('consultar_pedido', {'numero_pedido': 19}).data['observacoes'] == (
        'Pagamento não confirmado dentro do prazo')


def test_order_lookup_is_blocked_after_repeated_failures(make):
    tools = make()
    for attempt in range(MAX_FAILED_LOOKUPS):
        failed = tools.call('consultar_pedido', {'numero_pedido': attempt + 1, 'email': 'x@y.z'})
        assert failed.data == {'erro': ORDER_NOT_FOUND}  # the limit-th failure is still an ordinary one
    blocked = tools.call('consultar_pedido', {'numero_pedido': 8, 'email': ANA})
    assert 'bloqueada' in blocked.data['erro'] and blocked.events == ()
    assert make(session='s2').call('consultar_pedido', {'numero_pedido': 8, 'email': ANA}).events == (ShowOrder(8),)


# --- robustness ----------------------------------------------------------------------------------------------------

def test_malformed_calls_come_back_as_errors(tools):
    assert 'desconhecida' in tools.call('apagar_banco', {}).data['erro']
    assert 'JSON' in tools.call('ver_produto', '{produto_id: 81').data['erro']
    assert 'JSON' in tools.call('ver_produto', '[81]').data['erro']
    assert tools.call('ver_produto', '{}').data == {'erro': 'Faltou informar: produto_id.'}
    assert tools.call('ver_produto', '{"produto_id": 81, "extra": 1}').events == (OpenProduct(81),)


def test_huge_or_deeply_nested_arguments_are_refused_fast(tools):
    assert 'longos' in tools.call('ver_produto', '{"produto_id": "' + ' ' * 40000 + 'x"}').data['erro']
    assert 'longos' in tools.call('ver_produto', '[' * 200000).data['erro']
    assert 'JSON' in tools.call('ver_produto', '[' * 1999).data['erro']


def test_a_tool_failure_is_logged_without_the_customers_data(tools, monkeypatch, caplog):
    def broken(*_args):
        raise RuntimeError('boom')
    monkeypatch.setattr('emporio.agent.tools.views.order_view', broken)
    result = tools.call('consultar_pedido', {'numero_pedido': 8, 'email': ANA})
    assert 'Falha interna' in result.data['erro']
    assert 'consultar_pedido' in caplog.text and ANA not in caplog.text
    caplog.clear()


def test_infinity_is_not_a_price(tools):
    assert 'erro' in tools.call('buscar_produtos', '{"preco_maximo": Infinity}').data


@pytest.mark.parametrize('value', [None, '', 0, -5, 3.7, True, 'x', [], {}, [1, 2], {'a': 1}, '9' * 30])
def test_no_tool_raises_on_odd_arguments(tools, value):
    for schema in tools.schemas():
        name = schema['function']['name']
        for key in schema['function']['parameters']['properties']:
            result = tools.call(name, {key: value})
            assert 'Falha interna' not in result.data.get('erro', '')  # handled, not a crash caught by call()
            json.dumps(result.data)


def test_every_allowed_number_of_installments_comes_ready(make):
    details = make().call('ver_produto', {'produto_id': 92}).data
    assert details['parcelas_disponiveis']['5x'] == 'R$ 259,80' and details['parcelas_disponiveis']['12x'] == 'R$ 108,25'
    assert '13x' not in details['parcelas_disponiveis']
    found = make().call('buscar_produtos', {'consulta': 'FG800'}).data['produtos'][0]
    assert found['parcelas_disponiveis']['5x'] == 'R$ 259,80'


def test_a_product_not_in_the_catalog_brings_what_is_similar(make):
    data = make().call('buscar_produtos', {'consulta': 'Gibson SG Standard'}).data
    assert data['encontrados'] == 0 and data['parecidos'][0]['nome'].startswith('Gibson')
    assert 'nunca outra marca' in data['aviso']


def test_accessories_are_understood_in_english_and_french(make):
    assert 'escopo' in make().call('buscar_produtos', {'consulta': 'guitar strings'}).data
    assert 'escopo' in make().call('buscar_produtos', {'consulta': 'cordes'}).data
    assert 'escopo' not in make().call('buscar_produtos', {'consulta': '7 strings'}).data


def test_the_model_chooses_categories_in_the_page_language(store, guard):
    english = Toolbox(store, AgentContext('s', now=NOW, language='en'), guard)
    choices = english.schemas()[0]['function']['parameters']['properties']['categoria']['enum']
    assert 'Guitars' in choices and 'Electric guitars' in choices and 'Guitarras' not in choices
    for name in ('Guitars', 'Violões', 'Guitares'):
        assert english.call('buscar_produtos', {'categoria': name, 'preco_maximo': 1000}).data['encontrados'] == 12
    assert english.call('buscar_produtos', {'categoria': 'Electric guitars'}).data['encontrados'] == 5
