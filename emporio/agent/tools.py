"""Tools the agent calls: deterministic Python over the store data, the cart and the policy manual.

The model sees each tool's name, description and JSON Schema (`Toolbox.schemas()`) and calls it with JSON
arguments. `Toolbox.call` validates them and never raises: a bad call comes back as {'erro': ...}, so the model can
fix it or explain the problem to the customer. Storefront actions (highlight products, change the cart, open an
order) come back as events for the agent to stream to the interface.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from .. import policies
from ..data import STOPWORDS, Category, Product, StoreData, normalize
from ..format import brl
from ..i18n import LANGUAGES, Translator
from . import views
from .base import AgentContext
from .events import ShowProducts, UIAction

log = logging.getLogger(__name__)

MAX_LISTED = 15  # products described to the model per search; the storefront highlights all of them


class ToolError(Exception):
    """A call the tool can't carry out; the message goes back to the model."""


@dataclass(frozen=True)
class ToolResult:
    data: dict  # what the model reads
    events: tuple[UIAction, ...] = ()  # what the storefront does
    suggestions: tuple[str, ...] = ()  # quick replies offered to the customer after the answer


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments
    run: Callable[..., ToolResult]


class Toolbox:
    """The tools for one turn of one conversation."""

    def __init__(self, store: StoreData, context: AgentContext) -> None:
        self.store = store
        self.context = context
        self.t = Translator(context.language)  # what the customer reads directly
        self.tools = {tool.name: tool for tool in self._tools()}

    def schemas(self) -> list[dict]:
        """The tools in the OpenAI function-calling format."""
        return [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
                                                  'parameters': t.parameters}} for t in self.tools.values()]

    def call(self, name: str, arguments: str | dict | None) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult({'erro': f'Ferramenta desconhecida: {name}.'})
        try:
            args = json.loads(arguments or '{}') if isinstance(arguments, str) else dict(arguments or {})
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        if not isinstance(args, dict):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        args = {key: value for key, value in args.items()
                if key in tool.parameters['properties'] and value not in (None, '')}
        if missing := [key for key in tool.parameters.get('required', ()) if key not in args]:
            return ToolResult({'erro': f'Faltou informar: {", ".join(missing)}.'})
        try:
            return tool.run(**args)
        except ToolError as error:
            return ToolResult({'erro': str(error)})
        except Exception:  # a bug must not end the conversation
            log.exception('tool %s failed with arguments %s', name, sorted(args))  # names only: values may be e-mails
            return ToolResult({'erro': 'Falha interna nesta consulta. Peça desculpas e ofereça o contato da equipe: '
                                       f'{policies.STORE.phone}.'})

    # --- catalog -------------------------------------------------------------------------------------------------

    def search_products(self, consulta: str = '', categoria: str = '', preco_maximo: float | None = None,
                        em_promocao: bool = False, so_disponiveis: bool = True, ordem: str = '') -> ToolResult:
        query = _text(consulta, 'consulta') if consulta else ''
        words = re.findall(r'[a-z0-9]+', normalize(query))
        if not set(words) - STOPWORDS:  # '???' or 'de' say nothing, and an empty query means the whole catalog
            query = ''
        category = self._category(categoria) if categoria else None
        max_price = _number(preco_maximo, 'preco_maximo') if preco_maximo is not None else None
        on_sale = _boolean(em_promocao, 'em_promocao')
        only_available = _boolean(so_disponiveis, 'so_disponiveis')
        if not (query or category or max_price or on_sale):
            raise ToolError('Informe ao menos consulta, categoria, preco_maximo ou em_promocao.')

        def matching(*, only_available: bool) -> list[Product]:
            products = self.store.search(query, category and category.id, max_price, only_available, prefix_last=False)
            return [p for p in products if p.promotion] if on_sale else products

        found = matching(only_available=only_available)
        if ordem in ('menor_preco', 'maior_preco'):
            found.sort(key=lambda p: p.price, reverse=ordem == 'maior_preco')
        data: dict = {'encontrados': len(found),
                      'produtos': [views.product_summary(p, self.store) for p in found[:MAX_LISTED]]}
        if 0 < len(found) <= 3:  # 'and in 5x?' usually comes with the price, in the same message
            for view, product in zip(data['produtos'], found, strict=True):
                view['parcelas_disponiveis'] = views.installment_options(product.price)
        title = ' '.join(filter(None, [self.t.category_name(category) if category else self.t('agent.products'),
                                       self.t('agent.on_sale') if on_sale else '',
                                       f'"{query}"' if query else '',
                                       self.t('agent.up_to', amount=brl(max_price)) if max_price is not None else '']))
        events = (ShowProducts(tuple(p.id for p in found), title),) if found else ()
        if len(found) > MAX_LISTED:
            data['observacao'] = f'Só os {MAX_LISTED} primeiros estão listados. A vitrine destaca os {len(found)}.'
        return ToolResult(data, events, (self.t('suggestion.on_sale'), self.t('suggestion.recommend')) if found else ())

    # --- argument checks -----------------------------------------------------------------------------------------

    def _category(self, value: object) -> Category:
        wanted = normalize(_text(value, 'categoria'))
        for category in self.store.categories_in_use:  # whatever language the model answers with
            names = {category.name} | {views.category_choice(category, Translator(language)) for language in LANGUAGES}
            if wanted in {normalize(name) for name in names} or wanted == normalize(category.name).split(' (')[0]:
                return category
        names = ', '.join(c.name for c in self.store.categories_in_use)
        raise ToolError(f'Categoria desconhecida: {value}. Use uma destas: {names}.')

    def _tools(self) -> list[Tool]:
        categories = [views.category_choice(c, self.t) for c in self.store.categories_in_use]
        return [
            Tool('buscar_produtos',
                 'Busca instrumentos no catálogo. Use sempre que o cliente procurar, comparar ou perguntar preço, '
                 'estoque ou promoção de produtos: preço e estoque só valem consultados aqui. Os resultados aparecem '
                 'destacados na vitrine. Para o tipo de instrumento use `categoria`, para orçamento `preco_maximo` e '
                 'para "o que está em promoção" `em_promocao`; em `consulta` vão só palavras como marca, modelo ou '
                 'característica ("Yamaha", "GD20", "aço", "eletroacústico", "7 cordas").',
                 _object({
                     'consulta': {'type': 'string', 'description': 'Marca, modelo ou característica, sem preço.'},
                     'categoria': {'type': 'string', 'enum': categories},
                     'preco_maximo': {'type': 'number', 'description': 'Preço máximo em reais, ex.: 1000.'},
                     'em_promocao': {'type': 'boolean', 'description': 'true para trazer só produtos em promoção.'},
                     'so_disponiveis': {'type': 'boolean', 'description': 'Padrão true. Use false para incluir '
                                        'esgotados, fora de linha e em breve.'},
                     'ordem': {'type': 'string', 'enum': ['menor_preco', 'maior_preco']},
                 }), self.search_products),
        ]


def _object(properties: dict, required: list[str] | None = None) -> dict:
    schema = {'type': 'object', 'properties': properties}
    if required:
        schema['required'] = required
    return schema


def _number(value: object, name: str) -> float:
    if isinstance(value, str) and re.fullmatch(r'\s*\d+(\.\d{1,2})?\s*', value):  # '1000' or '999.90', not '1.000'
        value = float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < float('inf'):
        return float(value)
    raise ToolError(f'{name} precisa ser um valor em reais maior que zero, recebi {value!r}.')


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ToolError(f'{name} precisa ser true ou false, recebi {value!r}.')


def _text(value: object, name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ToolError(f'{name} precisa ser um texto, recebi {value!r}.')
