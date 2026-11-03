"""Catalog search used by the storefront header and by the agent."""
import pytest

STEEL_STRING_GUITARS = [86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 103, 104, 105, 106, 107, 108, 112]


def ids(products):
    return [p.id for p in products]


def test_steel_strings_come_from_the_specs_not_from_braco(store):
    # 'aço' used to match 'braço' in descriptions and returned nylon guitars
    assert ids(store.search('aço')) == STEEL_STRING_GUITARS
    assert ids(store.search('violão de aço')) == STEEL_STRING_GUITARS
    assert ids(store.search('violão com cordas de aço')) == STEEL_STRING_GUITARS
    assert all(store.products[i].specs['strings'] == 'nylon' for i in ids(store.search('nylon', category_id=5)))


@pytest.mark.parametrize('query, expected', [
    ('gd20', [95]), ('gd-20', [95]), ('GD 20', [95]), ('Takamine GD-20', [95]),
    ('SN 7C', [113]), ('sn-7c', [113]), ('Martin D 28E', [108]), ('gf 1r', [88]), ('ka 15s', [116]),
])
def test_model_codes_with_hyphen_space_or_neither(store, query, expected):
    assert ids(store.search(query)) == expected


def test_model_code_prefix_lists_every_variant(store):
    assert ids(store.search('d28')) == ids(store.search('martin d-28')) == [93, 108]  # D-28 and D-28E


def test_plural_accents_and_punctuation(store):
    violoes = ids(store.search('violao'))
    assert len(violoes) == 36  # the 35 violões plus a baritone ukulele described as "entre o ukulele e o violão"
    assert ids(store.search('VIOLÕES')) == ids(store.search('violão?')) == violoes
    assert ids(store.search('sintetizadores')) == [142, 143, 144]
    assert ids(store.search('tenores')) == ids(store.search('tenor'))
    assert ids(store.search('captador', prefix_last=False)) == ids(store.search('captadores')) == [
        131, 132, 133, 134, 138, 145]
    assert ids(store.search('profissionais')) == ids(store.search('profissional', prefix_last=False))


def test_category_words_customers_use(store):
    assert ids(store.search('contrabaixo')) == ids(store.search('baixo elétrico')) == [135, 136, 137, 138]
    assert len(store.search('violão acústico')) == 36
    assert ids(store.search('guitarra elétrica')) == [131, 132, 133, 134, 145]


def test_whole_words_only(store):
    assert store.search('amplificador') == []  # 'pré-amplificador' is a guitar part, not an amplifier
    assert ids(store.search('pré-amplificador')) == [99, 105, 122]
    assert ids(store.search('single coil')) == [145]  # 'single-coil' in the description
    assert store.search('palheta') == []
    assert ids(store.search('kit 1')) == [139]  # numbers never match by prefix ('1' is not '100')


def test_last_word_matches_while_typing(store):
    assert len(store.search('viol')) == 36
    assert store.search('viol', prefix_last=False) == []


def test_specs_and_filters_together(store):
    assert ids(store.search('aço', category_id=5, max_price=1000, only_available=True)) == [86, 87, 88, 89, 91, 107, 112]
    assert {94, 108} <= set(ids(store.search('eletroacústico')))  # electronics only in the specs


def test_untrusted_descriptions_are_not_searched(store):
    assert [p.id for p in store.products.values() if not p.details_verified] == [135, 136, 137, 139, 140, 142, 143, 144]
    assert ids(store.search('fender')) == [134, 145]  # 135 is a Music Man described as a Fender Jazz Bass
    assert ids(store.search('bateria pearl')) == [140]  # 139 is a Yamaha described as a Pearl Export


def test_a_subtype_narrows_the_search(store):
    assert 81 not in ids(store.search('folk'))  # the Yamaha C40 is a nylon guitar
    assert all(0 < len(store.search(word, prefix_last=False)) < 15 for word in ('soprano', 'concert', 'tenor', 'baritone'))
    assert len(store.search('ukulele tenor', max_price=400, only_available=True)) == 1


def test_classical_means_a_nylon_guitar(store):
    classical = store.search('classical', prefix_last=False)
    assert classical and all(p.category_id == 5 and p.specs['strings'] == 'nylon' for p in classical)
    assert ids(classical) == ids(store.search('classique', prefix_last=False))
