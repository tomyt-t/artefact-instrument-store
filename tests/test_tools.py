"""Agent tools: what the model reads back and which storefront events each call produces."""
import json
from datetime import datetime

import pytest

from emporio.agent.base import AgentContext
from emporio.agent.events import OpenProduct, ShowProducts
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
        'buscar_produtos', 'ver_produto']
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


def test_the_note_about_highlighted_products_is_true(tools):
    highlighted = tools.call('buscar_produtos', {'categoria': 'Violões'}).data['observacao']
    assert highlighted == 'Só os 15 primeiros estão listados. A vitrine destaca os 33.'
    assert tools.call('buscar_produtos', {'consulta': 'cordas'}).data['observacao'] == (
        'Só os 15 primeiros estão listados.')  # nothing lights up for a search for strings


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


def test_the_model_chooses_categories_in_the_page_language(store):
    english = Toolbox(store, AgentContext('s', now=NOW, language='en'))
    choices = english.schemas()[0]['function']['parameters']['properties']['categoria']['enum']
    assert 'Guitars' in choices and 'Electric guitars' in choices and 'Guitarras' not in choices
    for name in ('Guitars', 'Violões', 'Guitares'):
        assert english.call('buscar_produtos', {'categoria': name, 'preco_maximo': 1000}).data['encontrados'] == 12
    assert english.call('buscar_produtos', {'categoria': 'Electric guitars'}).data['encontrados'] == 5
