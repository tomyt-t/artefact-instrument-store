"""Right-hand cart drawer. Re-renders on every cart change, whether it came from a click or from the agent."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ... import policies
from ...cart import Cart, CartLine
from ...format import brl
from ...i18n import Translator
from ..theme import product_image


class CartDrawer:
    def __init__(self, cart: Cart, t: Translator, *, on_ask: Callable[[str], None]) -> None:
        self.cart = cart
        self.t = t
        self.on_ask = on_ask
        with ui.right_drawer(value=False).props('width=400 bordered').classes('bg-cream p-0') as self.drawer:
            self.content()
        cart.on_change(self.content.refresh)

    def open(self, *_) -> str:
        self.drawer.show()
        return self.t('drawer.opened')

    @ui.refreshable_method
    def content(self) -> None:
        cart, t = self.cart, self.t
        with ui.column().classes('w-full h-full gap-0'):
            with ui.row().classes('w-full items-center px-5 py-4 border-b border-[#ece2d4]'):
                ui.label(t('drawer.title')).classes('font-display text-2xl')
                ui.badge(str(cart.count), color='primary').props('rounded')
                ui.space()
                ui.button(icon='close', on_click=self.drawer.hide).props('flat round dense')
            if not cart.lines:
                with ui.column().classes('w-full grow items-center justify-center gap-3 p-8 text-center'):
                    ui.icon('shopping_bag', size='4rem', color='grey-5')
                    ui.label(t('drawer.empty')).classes('text-lg font-semibold')
                    ui.label(t('drawer.empty_text')).classes('text-muted')
                    ui.button(t('drawer.suggest'), icon='forum',
                              on_click=lambda: (self.drawer.hide(), self.on_ask(t('drawer.suggest_question')))) \
                        .props('unelevated rounded no-caps')
                return
            with ui.scroll_area().classes('grow'):
                for line in cart.lines:
                    self._line(line)
            self._summary()

    def _line(self, line: CartLine) -> None:
        product, quantity, t = line.product, line.quantity, self.t
        product_id = product.id
        with ui.row().classes('w-full no-wrap items-center gap-3 px-5 py-3 border-b border-[#ece2d4]'):
            ui.image(product_image(product.image)).props('ratio=1 fit=contain no-spinner') \
                .classes('w-16 h-16 rounded-xl bg-sand shrink-0')
            with ui.column().classes('gap-1 grow min-w-0'):
                ui.label(t.product_name(product)).classes('text-sm font-medium leading-tight line-clamp-2')
                with ui.row().classes('items-center gap-1'):
                    ui.button(icon='remove', on_click=lambda: self.cart.set_quantity(product_id, quantity - 1)) \
                        .props('flat round dense size=sm')
                    ui.label(str(quantity)).classes('w-6 text-center font-semibold')
                    plus = ui.button(icon='add', on_click=lambda: self.cart.set_quantity(product_id, quantity + 1)) \
                        .props('flat round dense size=sm')
                    if quantity >= product.stock:
                        plus.props('disable')
            with ui.column().classes('items-end gap-1'):
                ui.label(brl(line.total)).classes('font-semibold whitespace-nowrap')
                if product.promotion:  # a promotional price always goes with the original and the percentage (6.2)
                    ui.label(brl(product.list_price * quantity)).classes('text-xs text-muted line-through')
                    ui.label(t('drawer.discount', percent=product.promotion.discount_percent)) \
                        .classes('text-xs text-negative')
                ui.button(icon='delete_outline', on_click=lambda: self.cart.remove(product_id)) \
                    .props('flat round dense size=sm color=grey-7')

    def _summary(self) -> None:
        cart, t = self.cart, self.t
        with ui.column().classes('w-full gap-2 px-5 py-4 bg-white border-t border-[#ece2d4]'):
            if cart.promo_savings:
                self._row(t('drawer.list_price'), brl(cart.list_total), 'text-muted')
                self._row(t('drawer.promo_savings'), f'- {brl(cart.promo_savings)}', 'text-negative')
            self._row(t('drawer.total'), brl(cart.total), 'text-lg font-bold')
            if cart.pix_total < cart.total:
                self._row(t('drawer.pix'), brl(cart.pix_total), 'text-positive font-semibold')
            notes = []
            if cart.pix_total < cart.total:
                notes.append(t('drawer.pix_note', percent=f'{policies.PIX_DISCOUNT:.0%}'))
            n = policies.max_installments(cart.total)
            if n > 1:
                notes.append(t('drawer.installments', n=n, amount=brl(cart.total / n)))
            for note in notes:
                ui.label(note).classes('text-xs text-muted -mt-1')
            ui.separator().classes('my-1')
            if policies.local_shipping_fee(cart.total) == 0:
                ui.label(t('drawer.free_shipping')).classes('text-sm text-positive font-medium')
            else:
                ui.label(t('drawer.missing', limit=brl(policies.FREE_SHIPPING_ABOVE),
                           missing=brl(cart.missing_for_free_shipping))).classes('text-sm')
                ui.linear_progress(value=cart.total / policies.FREE_SHIPPING_ABOVE, show_value=False) \
                    .props('rounded color=accent track-color=grey-3 size=6px')
            ui.label(t('drawer.other_cities')).classes('text-xs text-muted')
            if cart.needs_freight_quote:
                ui.label(t('drawer.freight_quote')).classes('text-xs text-primary')
            ui.button(t('drawer.checkout'), icon='lock', on_click=lambda: ui.notify(
                t('drawer.checkout_note'), type='info')) \
                .props('unelevated rounded no-caps size=lg').classes('w-full mt-2')

    @staticmethod
    def _row(label: str, value: str, classes: str = '') -> None:
        with ui.row().classes(f'w-full justify-between {classes}'):
            ui.label(label)
            ui.label(value)
