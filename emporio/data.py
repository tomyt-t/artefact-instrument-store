"""Loads the store CSVs into typed, read-only objects shared by the web UI and the agent."""
from __future__ import annotations

import csv
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property, lru_cache
from pathlib import Path

from . import policies
from .i18n import STORE_LANGUAGE, Translator, translated_words

DATA_DIR = Path(os.environ.get('EMPORIO_DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))

BRANDS = ('Music Man', 'Yamaha', 'Tagima', 'Giannini', 'Rozini', 'Shelby', 'Crafter', 'Martin', 'Taylor',
          'Takamine', 'Kalani', 'Kala', 'Ohana', 'Fender', 'Gibson', 'Ibanez', 'PRS', 'Pearl', 'Korg', 'Nord', 'Roland')

ORDER_STATUS_LABELS = {
    'pending': 'Aguardando pagamento',
    'confirmed': 'Pagamento confirmado',
    'shipped': 'Enviado',
    'delivered': 'Entregue',
    'cancelled': 'Cancelado',
}

PAYMENT_LABELS = {'pix': 'PIX', 'debit': 'Cartão de débito', 'boleto': 'Boleto bancário'}

# In these rows the description is of another instrument: another brand or model (e.g. 135 'Music Man Bass 1X'
# described as a Fender Jazz Bass) or another kind of keyboard. The specs are values repeated across rows, and in
# most of them they also contradict the description. Only name, price, stock and status are trusted.
UNVERIFIED_DETAILS = frozenset({135, 136, 137, 139, 140, 142, 143, 144})

STOPWORDS = frozenset({'a', 'o', 'as', 'os', 'e', 'de', 'da', 'do', 'das', 'dos', 'em', 'no', 'na', 'um', 'uma',
                       'com', 'para', 'por', 'ate',
                       'the', 'an', 'of', 'and', 'for', 'with', 'to', 'in',
                       'le', 'la', 'les', 'l', 'd', 'des', 'du', 'et', 'pour', 'avec', 'au', 'aux', 'un', 'une'})
# Words customers use for a whole category, which the (often English) product names lack
CATEGORY_WORDS = {1: 'eletrica', 2: 'contrabaixo eletrico', 5: 'acustico', 9: 'acustico'}
SPEC_WORDS = {('strings', 'steel'): 'corda aco', ('strings', 'nylon'): 'corda nylon',
              ('electronics', 'yes'): 'eletroacustico'}
HYPHEN_PREFIXES = ('pre', 'pos', 'semi')  # 'pré-amplificador' is a part, not an amplifier


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    description: str
    is_active: bool


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category_id: int
    description: str
    list_price: float
    stock: int
    status: str  # active | discontinued | coming_soon
    specs: dict[str, str]
    created_at: date
    promotion: Promotion | None = None

    @property
    def is_available(self) -> bool:
        return self.status == 'active' and self.stock > 0

    @property
    def price(self) -> float:
        """Current selling price, with the active promotion applied."""
        if self.promotion:
            return round(self.list_price * (1 - self.promotion.discount_percent / 100), 2)
        return self.list_price

    @property
    def pix_price(self) -> float:
        """PIX gives 5% off the list price, but never on top of a promotion (policy 6.2)."""
        return self.price if self.promotion else round(self.list_price * (1 - policies.PIX_DISCOUNT), 2)

    @property
    def details_verified(self) -> bool:
        """False when the description is of another instrument and the specs can't settle it (UNVERIFIED_DETAILS)."""
        return self.id not in UNVERIFIED_DETAILS

    @property
    def brand(self) -> str:
        return next((b for b in BRANDS if b.lower() in self.name.lower()), '')

    @property
    def availability(self) -> str:
        if self.status == 'discontinued':
            return 'discontinued'
        if self.status == 'coming_soon':
            return 'coming_soon'
        if self.stock == 0:
            return 'sold_out'
        if self.stock <= 2:
            return 'last_units'
        return 'in_stock'

    @property
    def availability_label(self) -> str:
        return Translator(STORE_LANGUAGE)(f'availability.{self.availability}')

    @property
    def image(self) -> str:
        """Slug of the illustrative photo: one per instrument type, since the dataset has no images."""
        name = self.name.lower()
        match self.category_id:
            case 1: return 'guitarra-eletrica'
            case 2: return 'baixo-eletrico'
            case 3: return 'bateria-acustica'
            case 4: return 'teclado-sintetizador'
            case 9: return 'ukulele'
        if '7 cordas' in name:
            return 'violao-7-cordas'
        if self.specs.get('electronics') == 'yes':
            return 'violao-eletroacustico'
        if 'dreadnought' in name:
            return 'violao-dreadnought'
        if self.specs.get('strings') == 'steel':
            return 'violao-folk-aco'
        return 'violao-classico-nylon'


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: str
    total: float
    payment_method: str
    tracking_code: str
    estimated_delivery: date | None
    notes: str
    items: tuple[tuple[int, int], ...] = field(default=())  # (product_id, quantity)

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS.get(self.status, self.status)

    @property
    def payment_label(self) -> str:
        if self.payment_method.startswith('credit_'):
            return f'Cartão de crédito em {self.payment_method.removeprefix("credit_")}'
        return PAYMENT_LABELS.get(self.payment_method, self.payment_method)


@dataclass(frozen=True)
class StoreData:
    categories: dict[int, Category]
    products: dict[int, Product]
    customers: dict[int, Customer]
    orders: dict[int, Order]

    @cached_property
    def categories_in_use(self) -> list[Category]:
        used = {p.category_id for p in self.products.values()}
        return [c for c in self.categories.values() if c.id in used]

    @cached_property
    def _search_index(self) -> dict[int, frozenset[str]]:
        """Words of each product's name, category, description (when trusted) and specs."""
        index = {}
        for product in self.products.values():
            category = product.category_id
            text = f'{product.name} {self.categories[category].name} {CATEGORY_WORDS.get(category, "")}'
            if product.details_verified:
                text += f' {product.description}'
            specs = ' '.join(words for (key, value), words in SPEC_WORDS.items() if product.specs.get(key) == value)
            index[product.id] = frozenset(_index_words(f'{text} {specs} {translated_words(product, category)}'))
        return index

    def search(self, query: str = '', category_id: int | None = None, max_price: float | None = None,
               only_available: bool = False, *, prefix_last: bool = True) -> list[Product]:
        """Products that have every word of `query` (accents, case and plurals ignored; 'gd-20' finds 'GD20').

        With `prefix_last`, the last word may also be the start of a word, so results show up while typing.
        """
        terms = [token for token in _tokens(query) if token not in STOPWORDS]
        if prefix_last and terms and any(_has_words(words, terms[-1:], False) for words in self._search_index.values()):
            prefix_last = False  # already a whole word: 'aço' must not find 'acoustic' just because it starts the same
        result = []
        for product in self.products.values():
            if terms and not _has_words(self._search_index[product.id], terms, prefix_last):
                continue
            if category_id is not None and product.category_id != category_id:
                continue
            if max_price is not None and product.price > max_price:
                continue
            if only_available and not product.is_available:
                continue
            result.append(product)
        return result

    def find_order(self, order_id: int, email: str) -> Order | None:
        """Orders are only disclosed to whoever knows the customer's e-mail (LGPD, policy 9)."""
        order = self.orders.get(order_id)
        if order and self.customers[order.customer_id].email.lower() == email.strip().lower():
            return order
        return None


def normalize(text: str) -> str:
    """Lowercase and strip accents so 'violao' matches 'Violão'."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    """Normalized words. A hyphenated word gives its pieces and the joined word last ('SN-7C' -> sn, 7c, sn7c),
    except after prefixes like 'pré-', where only the joined word counts."""
    tokens = []
    for word in re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*', normalize(text)):
        pieces = word.split('-')
        if len(pieces) > 1 and pieces[0] not in HYPHEN_PREFIXES:
            tokens += pieces
        tokens.append(''.join(pieces))
    return tokens


def _forms(word: str) -> set[str]:
    """The word and its possible singulars ('violões' -> violao, 'captadores' -> captador, 'musicais' -> musical)."""
    forms = {word}
    if word.endswith(('oes', 'aes', 'aos')):
        forms.add(word[:-3] + 'ao')
    if len(word) > 3 and word.endswith('s'):
        forms.add(word[:-1])
    if len(word) > 4:
        if word.endswith(('res', 'zes')):
            forms.add(word[:-2])
        elif word.endswith('ais'):
            forms.add(word[:-3] + 'al')
        elif word.endswith('eis'):
            forms.add(word[:-3] + 'el')
        elif word.endswith('ns'):
            forms.add(word[:-2] + 'm')
    return forms


def _index_words(text: str) -> set[str]:
    """Every form of every word, plus the letter and digit parts of model codes ('gd20' also gives 'gd' and '20')."""
    words = {form for token in _tokens(text) for form in _forms(token)}
    return words | {part for word in words if not word.isalpha() for part in re.findall(r'[a-z]+|[0-9]+', word)}


def _has_words(words: frozenset[str], terms: list[str], prefix_last: bool) -> bool:
    *rest, last = terms
    if not all(_forms(term) & words for term in rest):
        return False
    if _forms(last) & words:
        return True
    # numbers never match by prefix: 'Kit 1' must not find '100'
    return prefix_last and len(last) >= 3 and not last.isdigit() and any(w.startswith(last) for w in words)


@lru_cache(maxsize=1)
def load_store(data_dir: Path = DATA_DIR) -> StoreData:
    categories = {int(r['category_id']): Category(int(r['category_id']), r['name'], r['description'])
                  for r in _rows(data_dir, 'categories')}
    promotions = {}
    for r in _rows(data_dir, 'promotions'):
        if r['is_active'] == '1':  # inactive rows are history and must never be offered (policy 7.3)
            promo = Promotion(int(r['promotion_id']), int(r['product_id']), int(r['discount_percent']),
                              r['description'], True)
            promotions[promo.product_id] = promo
    products = {}
    for r in _rows(data_dir, 'products'):
        pid = int(r['product_id'])
        products[pid] = Product(
            id=pid, name=r['name'].strip(), category_id=int(r['category_id']), description=r['description'].strip(),
            list_price=float(r['price_brl']), stock=int(r['stock_quantity']), status=r['status'].strip(),
            specs={k: str(v) for k, v in json.loads(r['specs']).items()},
            created_at=date.fromisoformat(r['created_at']), promotion=promotions.get(pid),
        )
    customers = {int(r['customer_id']): Customer(int(r['customer_id']), r['name'], r['phone'], r['email'], r['city'])
                 for r in _rows(data_dir, 'customers')}
    items: dict[int, list[tuple[int, int]]] = {}
    for r in _rows(data_dir, 'order_items'):
        items.setdefault(int(r['order_id']), []).append((int(r['product_id']), int(r['quantity'])))
    orders = {}
    for r in _rows(data_dir, 'orders'):
        oid = int(r['order_id'])
        orders[oid] = Order(
            id=oid, customer_id=int(r['customer_id']), order_date=date.fromisoformat(r['order_date']),
            status=r['status'], total=float(r['total_brl']), payment_method=r['payment_method'],
            tracking_code=r['tracking_code'], notes=r['notes'],
            estimated_delivery=date.fromisoformat(r['estimated_delivery']) if r['estimated_delivery'] else None,
            items=tuple(items.get(oid, ())),
        )
    return StoreData(categories, dict(sorted(products.items())), customers, orders)


def _rows(data_dir: Path, table: str) -> list[dict[str, str]]:
    path = next(data_dir.glob(f'*{table}.csv'))
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))
