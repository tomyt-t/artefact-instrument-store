"""Product details, opened by a click on a card."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ...data import Product, StoreData
from ...i18n import Translator
from ...policies import STORE
from ..theme import product_image
from .product_card import availability_badge, price_block


class ProductDialog:
    def __init__(self, store: StoreData, t: Translator, *, on_add: Callable[[int], None],
                 on_ask: Callable[[str], None]) -> None:
        self.store = store
        self.t = t
        self.on_add = on_add
        self.on_ask = on_ask
        self.product: Product | None = None
        with ui.dialog() as self.dialog, ui.card().classes('w-full max-w-4xl p-0 rounded-3xl overflow-hidden'):
            self.content()

    def open(self, product_id: int) -> str | None:
        self.product = self.store.products.get(product_id)
        if self.product is None:
            return None
        self.content.refresh()
        self.dialog.open()
        return self.t('product.opened', name=self.t.product_name(self.product))

    @ui.refreshable_method
    def content(self) -> None:
        product, t = self.product, self.t
        if product is None:
            return
        category = t.category_name(self.store.categories[product.category_id])
        name = t.product_name(product)
        with ui.row().classes('w-full no-wrap max-md:flex-col gap-0 items-stretch'):
            with ui.column().classes('md:w-1/2 w-full bg-sand items-center justify-center p-6 relative'):
                ui.image(product_image(product.image)).props('ratio=1 fit=contain no-spinner') \
                    .classes('product-photo w-full max-w-sm')
                ui.label(t('product.illustrative')).classes('absolute bottom-3 left-4 text-xs text-muted')
            with ui.column().classes('md:w-1/2 w-full p-6 gap-3'):
                with ui.row().classes('w-full items-center'):
                    ui.label(f'{product.brand} · {category}'.upper()).classes('text-xs tracking-wider text-muted')
                    ui.space()
                    ui.button(icon='close', on_click=self.dialog.close).props('flat round dense')
                ui.label(name).classes('font-display text-3xl leading-tight')
                availability_badge(product, t)
                price_block(product, t, large=True)
                ui.label(t.description(product) if product.details_verified else t('product.unverified', phone=STORE.phone)) \
                    .classes('text-[15px] leading-relaxed')
                with ui.element('div').classes('grid grid-cols-2 gap-x-6 gap-y-1 text-sm w-full'):
                    for key, value in (product.specs if product.details_verified else {}).items():
                        ui.label(t(f'spec.{key}')).classes('text-muted')
                        ui.label(t.spec_value(value))
                if product.is_available and product.stock <= 2:
                    ui.label(t('product.last_units', count=product.stock)).classes('text-sm text-primary font-medium')
                with ui.row().classes('w-full gap-2 mt-2'):
                    add = ui.button(t('product.add_to_cart'), icon='add_shopping_cart',
                                    on_click=lambda: (self.on_add(product.id), self.dialog.close())) \
                        .props('unelevated rounded no-caps size=md').classes('grow')
                    if not product.is_available:
                        add.props('disable')
                    ui.button(t('product.ask_button'), icon='forum',
                              on_click=lambda: (self.dialog.close(), self.on_ask(t('product.ask', name=name)))) \
                        .props('outline rounded no-caps color=secondary')
