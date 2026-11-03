"""Translations: one JSON per language, all with the same keys, plus the catalog's text in `catalog.<language>.json`.

The site opens in English. Portuguese is the store's own language: the CSVs are the catalog in Portuguese, and what the
model reads stays in Portuguese. Money keeps the Brazilian format everywhere, because the store sells in reais.
"""
from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / 'locales'
LANGUAGES = ('en', 'fr', 'pt-BR')
DEFAULT_LANGUAGE = 'en'
STORE_LANGUAGE = 'pt-BR'


def _read(name: str) -> dict:
    return json.loads((LOCALES / name).read_text(encoding='utf-8'))


@cache
def _messages(language: str) -> dict[str, str]:
    return _read(f'{language}.json')


@cache
def _catalog(language: str) -> dict:
    return {} if language == STORE_LANGUAGE else _read(f'catalog.{language}.json')


class Translator:
    """The texts of one language. An unknown language falls back to the default; an unknown key is a bug and raises."""

    def __init__(self, language: str) -> None:
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE

    def __call__(self, key: str, **values: object) -> str:
        return _messages(self.language)[key].format(**values)

    def date(self, day: date) -> str:
        if self.language == 'en':
            return f'{day:%b} {day.day}, {day.year}'
        return f'{day:%d/%m/%Y}'

    def hour(self, hour: int) -> str:
        if self.language == 'en':
            return f'{hour % 12 or 12} {"a.m." if hour < 12 else "p.m."}'
        return f'{hour}h'

    def payment(self, method: str) -> str:
        if method.startswith('credit_'):
            return self('payment.credit', plan=method.removeprefix('credit_'))
        key = f'payment.{method}'
        return self(key) if key in _messages(self.language) else method

    # --- catalog: what the CSVs say, in this language ----------------------------------------------------------------

    def _catalog_text(self, section: str, key: object, fallback: str) -> str:
        return _catalog(self.language).get(section, {}).get(str(key), fallback)

    def category_name(self, category) -> str:
        return _catalog(self.language).get('categories', {}).get(str(category.id), {}).get('name', category.name)

    def product_name(self, product) -> str:
        name = product.name
        words = _catalog(self.language).get('name_words', {})
        for word in sorted(words, key=len, reverse=True):  # 'Bateria Acústica' before any shorter word inside it
            name = name.replace(word, words[word])
        return name

    def description(self, product) -> str:
        return self._catalog_text('products', product.id, product.description)

    def promotion(self, description: str) -> str:
        return self._catalog_text('promotions', description, description)

    def spec_value(self, value: str) -> str:
        return self._catalog_text('specs', value, value)

    def order_note(self, note: str) -> str:
        return self._catalog_text('order_notes', note, note)


def translated_words(product, category_id: int) -> str:
    """The product in the other languages, so the search finds 'guitar' or 'guitare' whatever the page's language."""
    words = []
    for language in LANGUAGES:
        if language != STORE_LANGUAGE:
            translator = Translator(language)
            category = _catalog(language)['categories'][str(category_id)]
            words += [translator.product_name(product), category['name'], category['keywords']]
            if product.details_verified:  # specs, not the description: 'deep bass' on a drum kit is not a bass
                words += [translator.spec_value(value) for key, value in product.specs.items() if key != 'electronics']
                if product.specs.get('strings') in ('steel', 'nylon'):
                    words.append(_catalog(language)['strings_word'])
    return ' '.join(words)
