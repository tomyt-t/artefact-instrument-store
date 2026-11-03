"""Order tracking: a lookup form (order number + e-mail) and the order panel."""
from __future__ import annotations

from datetime import datetime

from nicegui import ui

from ...data import Order, StoreData
from ...format import brl
from ...i18n import Translator
from ...policies import TIMEZONE, delivery_days_late
from ..theme import product_image

STEPS = [('pending', 'receipt_long'), ('confirmed', 'payments'), ('shipped', 'local_shipping'), ('delivered', 'home')]
STEP_INDEX = {status: i for i, (status, _) in enumerate(STEPS)}


class OrderDialog:
    def __init__(self, store: StoreData, t: Translator) -> None:
        self.store = store
        self.t = t
        self.order: Order | None = None
        with ui.dialog() as self.dialog, ui.card().classes('w-full max-w-xl rounded-3xl p-6'):
            self.content()
        with ui.dialog() as self.lookup, ui.card().classes('w-full max-w-sm rounded-3xl p-6 gap-3'):
            ui.label(t('order.lookup_title')).classes('font-display text-2xl')
            ui.label(t('order.lookup_text')).classes('text-sm text-muted')
            number = ui.number(t('order.number'), format='%d', min=1).props('outlined rounded').classes('w-full')
            email = ui.input(t('order.email')).props('outlined rounded type=email').classes('w-full')
            error = ui.label().classes('text-sm text-negative')

            def search() -> None:
                order = self.store.find_order(int(number.value or 0), email.value or '')
                if order is None:
                    error.set_text(t('order.not_found'))
                    return
                error.set_text('')
                self.lookup.close()
                self.open(order.id)
            email.on('keydown.enter', search)
            ui.button(t('order.search'), icon='search', on_click=search).props('unelevated rounded no-caps').classes('w-full')

    def open(self, order_id: int) -> str | None:
        self.order = self.store.orders.get(order_id)
        if self.order is None:
            return None
        self.content.refresh()
        self.dialog.open()
        return self.t('order.opened', id=order_id)

    @ui.refreshable_method
    def content(self) -> None:
        order, t = self.order, self.t
        if order is None:
            return
        customer = self.store.customers[order.customer_id]
        with ui.row().classes('w-full items-start'):
            with ui.column().classes('gap-0'):
                ui.label(t('order.title', id=order.id)).classes('font-display text-3xl')
                ui.label(t('order.made_on', name=customer.first_name, date=t.date(order.order_date))).classes('text-muted')
            ui.space()
            ui.button(icon='close', on_click=self.dialog.close).props('flat round dense')
        if order.status == 'cancelled':
            with ui.row().classes('w-full items-center gap-3 rounded-2xl bg-red-50 p-4 no-wrap'):
                ui.icon('cancel', color='negative', size='md')
                with ui.column().classes('gap-0'):
                    ui.label(t('order.cancelled')).classes('font-semibold text-negative')
                    ui.label(t.order_note(order.notes) if order.notes else t('order.contact')).classes('text-sm')
        else:
            self._progress(STEP_INDEX[order.status])
        with ui.column().classes('w-full gap-2'):
            for product_id, quantity in order.items:
                product = self.store.products[product_id]
                with ui.row().classes('w-full items-center no-wrap gap-3'):
                    ui.image(product_image(product.image)).props('ratio=1 fit=contain no-spinner') \
                        .classes('w-14 h-14 rounded-xl bg-sand shrink-0')
                    ui.label(f'{quantity}× {t.product_name(product)}').classes('grow text-sm')
        ui.separator()
        with ui.element('div').classes('grid grid-cols-2 gap-y-1 text-sm w-full'):
            ui.label(t('order.total')).classes('text-muted')
            ui.label(brl(order.total)).classes('font-semibold')
            ui.label(t('order.payment')).classes('text-muted')
            ui.label(t.payment(order.payment_method))
            if order.tracking_code:
                ui.label(t('order.tracking')).classes('text-muted')
                with ui.row().classes('items-center gap-1'):
                    ui.label(order.tracking_code).classes('font-mono')
                    ui.button(icon='content_copy', on_click=lambda: ui.clipboard.write(order.tracking_code)) \
                        .props('flat round dense size=sm').tooltip(t('order.copy'))
            if order.estimated_delivery and order.status != 'delivered':
                ui.label(t('order.estimated')).classes('text-muted')
                late = delivery_days_late(order.estimated_delivery, datetime.now(TIMEZONE).date())
                day = t.date(order.estimated_delivery)
                ui.label(t('order.late', date=day) if late else day)

    def _progress(self, current: int) -> None:
        with ui.row().classes('w-full no-wrap items-start my-2'):
            for i, (status, icon) in enumerate(STEPS):
                done = i <= current
                with ui.column().classes('items-center gap-1 grow basis-0 text-center'):
                    ui.icon(icon, size='sm').classes('rounded-full p-2') \
                        .classes('bg-primary text-white' if done else 'bg-grey-3 text-grey-6')
                    ui.label(self.t(f'order.step.{status}')).classes('text-xs leading-tight') \
                        .classes('font-semibold' if i == current else 'text-muted')
                if i < len(STEPS) - 1:
                    ui.element('div').classes('h-[3px] grow basis-0 mt-5 rounded') \
                        .classes('bg-primary' if i < current else 'bg-grey-3')
