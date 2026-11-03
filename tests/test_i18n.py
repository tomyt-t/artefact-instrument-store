"""The translations themselves: complete in every language and used correctly by the code."""
import json
import re
import string
from datetime import date
from pathlib import Path

import pytest

from emporio.i18n import DEFAULT_LANGUAGE, LANGUAGES, LOCALES, STORE_LANGUAGE, Translator

ROOT = Path(__file__).resolve().parent.parent
OTHER_LANGUAGES = [language for language in LANGUAGES if language != STORE_LANGUAGE]


def messages(language):
    return json.loads((LOCALES / f'{language}.json').read_text(encoding='utf-8'))


def placeholders(text):
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


@pytest.mark.parametrize('language', OTHER_LANGUAGES)
def test_every_language_has_the_same_keys_and_placeholders(language):
    base, other = messages(STORE_LANGUAGE), messages(language)
    assert set(other) == set(base)
    assert not [key for key in base if placeholders(other[key]) != placeholders(base[key])]


def test_every_key_the_code_asks_for_exists():
    known = set(messages(DEFAULT_LANGUAGE))
    prefixes = {key.rsplit('.', 1)[0] for key in known}
    for path in (ROOT / 'emporio').rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        assert not set(re.findall(r"\bt\('([a-z_.0-9]+)'", text)) - known, path
        assert not {prefix.rstrip('.') for prefix in re.findall(r"\bt\(f'([a-z_.]+)\{", text)} - prefixes, path


def test_the_site_opens_in_english_and_a_strange_language_falls_back_to_it():
    assert DEFAULT_LANGUAGE == 'en'
    assert Translator('klingon').language == 'en'


def test_dates_and_hours_read_right_in_each_language():
    day = date(2026, 11, 3)
    assert [Translator(language).date(day) for language in LANGUAGES] == ['Nov 3, 2026', '03/11/2026', '03/11/2026']
    assert [Translator(language).hour(18) for language in LANGUAGES] == ['6 p.m.', '18h', '18h']
    assert Translator('en').hour(9) == '9 a.m.'
