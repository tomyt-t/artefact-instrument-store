"""Agent tools: what the model reads back and which storefront events each call produces."""
import json
from datetime import datetime

import pytest

from emporio.agent.base import AgentContext
from emporio.agent.events import ShowProducts
from emporio.agent.tools import Toolbox
from emporio.policies import TIMEZONE

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TIMEZONE)  # Saturday, open until 13h; Monday 02/11 is Finados


@pytest.fixture
def make(store):
    def make(*, cart=None, now=NOW, verified=(), session='s1', viewing=None):
        context = AgentContext(session_id=session, cart=cart or {}, now=now, verified_orders=set(verified),
                               viewing_product_id=viewing)
        toolbox = Toolbox(store, context)
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
        'buscar_produtos']
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


def test_a_query_without_words_is_not_the_whole_catalog(tools):
    for query in ('???', 'de', 'com de', '  -  '):
        result = tools.call('buscar_produtos', {'consulta': query})
        assert 'erro' in result.data and result.events == (), query
    assert tools.call('buscar_produtos', {'consulta': '???', 'categoria': 'Baixos'}).data['encontrados'] == 3


def test_search_needs_a_valid_filter(tools):
    assert 'erro' in tools.call('buscar_produtos', {}).data
    assert 'Violões' in tools.call('buscar_produtos', {'categoria': 'Sopros'}).data['erro']
    assert 'erro' in tools.call('buscar_produtos', {'preco_maximo': '1.000'}).data  # ambiguous, never guessed
    assert tools.call('buscar_produtos', {'categoria': 'violões', 'preco_maximo': '1000'}).data['encontrados'] == 12


def test_conflicting_descriptions_are_flagged_in_searches_too(tools):
    [music_man] = tools.call('buscar_produtos', {'consulta': 'Music Man Bass 1X'}).data['produtos']
    assert 'confirmados com a equipe' in music_man['aviso'] and 'especificacoes' not in music_man


# --- robustness ----------------------------------------------------------------------------------------------------

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


def test_the_model_chooses_categories_in_the_page_language(store):
    english = Toolbox(store, AgentContext('s', now=NOW, language='en'))
    choices = english.schemas()[0]['function']['parameters']['properties']['categoria']['enum']
    assert 'Guitars' in choices and 'Electric guitars' in choices and 'Guitarras' not in choices
    for name in ('Guitars', 'Violões', 'Guitares'):
        assert english.call('buscar_produtos', {'categoria': name, 'preco_maximo': 1000}).data['encontrados'] == 12
    assert english.call('buscar_produtos', {'categoria': 'Electric guitars'}).data['encontrados'] == 5
