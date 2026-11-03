"""Product catalog with filters."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ...data import Product, StoreData
from ...i18n import Translator
from .product_card import product_card

SORTS = ('relevance', 'price_asc', 'price_desc', 'newest')


class CatalogView:
    def __init__(self, store: StoreData, t: Translator, *, on_open: Callable[[int], None],
                 on_add: Callable[[int], None]) -> None:
        self.store = store
        self.t = t
        self.on_open = on_open
        self.on_add = on_add
        self.query = ''
        self.category_id: int | None = None
        self.sort = 'relevance'
        self.only_available = False

        with ui.column().classes('w-full gap-4').props('id=catalogo'):
            with ui.row().classes('w-full items-end justify-between gap-4'):
                with ui.column().classes('gap-0'):
                    ui.label(t('catalog.title')).classes('font-display text-3xl')
                    self.count_label = ui.label().classes('text-muted')
                with ui.row().classes('items-center gap-3'):
                    ui.switch(t('catalog.available_only'), on_change=lambda e: self._set(only_available=e.value)) \
                        .props('color=primary dense')
                    ui.select({key: t(f'sort.{key}') for key in SORTS}, value=self.sort,
                              on_change=lambda e: self._set(sort=e.value)) \
                        .props('outlined dense rounded options-dense').classes('w-40 bg-white')
            self.chips()
            self.grid()

    def set_query(self, query: str) -> None:
        self._set(query=query or '')

    def _set(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self, key, value)
        self.chips.refresh()
        self.grid.refresh()

    def _products(self) -> list[Product]:
        products = self.store.search(self.query, self.category_id, only_available=self.only_available)
        match self.sort:
            case 'price_asc': products.sort(key=lambda p: p.price)
            case 'price_desc': products.sort(key=lambda p: -p.price)
            case 'newest': products.sort(key=lambda p: p.created_at, reverse=True)
            case _: products.sort(key=lambda p: (not p.is_available, p.category_id, p.price))
        return products

    @ui.refreshable_method
    def chips(self) -> None:
        t = self.t
        with ui.row().classes('gap-2'):
            self._chip(t('catalog.all'), None)
            for category in self.store.categories_in_use:
                self._chip(t.category_name(category), category.id)

    def _chip(self, label: str, category_id: int | None) -> None:
        selected = self.category_id == category_id
        ui.button(label, on_click=lambda: self._set(category_id=category_id)) \
            .props(f'rounded no-caps unelevated {"" if selected else "outline"}') \
            .props(f'color={"secondary" if selected else "grey-8"}').classes('px-4')

    @ui.refreshable_method
    def grid(self) -> None:
        t = self.t
        products = self._products()
        self.count_label.set_text(t('catalog.count_query', count=len(products), query=self.query) if self.query
                                  else t('catalog.count', count=len(products)))
        if not products:
            with ui.column().classes('w-full items-center py-16 text-muted'):
                ui.icon('search_off', size='3rem')
                ui.label(t('catalog.empty'))
            return
        with ui.element('div').classes('w-full grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4'):
            for product in products:
                product_card(product, t.category_name(self.store.categories[product.category_id]), t,
                             on_open=self.on_open, on_add=self.on_add)


def offers_strip(store: StoreData, t: Translator, *, on_open: Callable[[int], None],
                 on_add: Callable[[int], None]) -> None:
    offers = [p for p in store.products.values() if p.promotion and p.is_available]
    if not offers:
        return
    with ui.column().classes('w-full gap-4'):
        with ui.row().classes('items-baseline gap-3'):
            ui.label(t('offers.title')).classes('font-display text-3xl')
            ui.label(t('offers.subtitle')).classes('text-muted')
        with ui.element('div').classes('w-full grid grid-cols-2 md:grid-cols-4 gap-4'):
            for product in offers:
                product_card(product, t.category_name(store.categories[product.category_id]), t,
                             on_open=on_open, on_add=on_add)
