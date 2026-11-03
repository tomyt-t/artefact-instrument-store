"""Product card and the price/availability pieces reused by the product dialog."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ... import policies
from ...data import Product
from ...format import brl
from ...i18n import Translator
from ..theme import product_image

AVAILABILITY_COLORS = {'in_stock': 'positive', 'last_units': 'accent', 'sold_out': 'grey-7',
                       'coming_soon': 'secondary', 'discontinued': 'grey-7'}


def availability_badge(product: Product, t: Translator) -> None:
    ui.badge(t(f'availability.{product.availability}'), color=AVAILABILITY_COLORS[product.availability]) \
        .props('rounded').classes('px-2 py-1 font-medium')


def price_block(product: Product, t: Translator, *, large: bool = False) -> None:
    """Promotions always show the original price and the discount next to the new one (policy 6.2)."""
    big = 'text-3xl' if large else 'text-xl'
    with ui.column().classes('gap-0'):
        if product.promotion:
            with ui.row().classes('items-center gap-2'):
                ui.label(brl(product.list_price)).classes('text-sm line-through text-muted')
                if large:  # cards already carry the discount badge on the photo
                    ui.badge(f'-{product.promotion.discount_percent}%', color='negative').props('rounded')
            ui.label(brl(product.price)).classes(f'{big} font-bold text-ink')
            promotion = t.promotion(product.promotion.description)
            ui.label(t('price.no_extra_pix', promotion=promotion) if large else promotion) \
                .classes('text-xs text-primary font-medium')
        else:
            ui.label(brl(product.price)).classes(f'{big} font-bold text-ink')
            ui.label(t('price.pix', amount=brl(product.pix_price), percent=f'{policies.PIX_DISCOUNT:.0%}')) \
                .classes('text-xs text-positive font-medium')
        n = policies.max_installments(product.price)
        if n > 1:
            ui.label(t('price.installments', n=n, amount=brl(product.price / n))).classes('text-xs text-muted')


def product_card(product: Product, category: str, t: Translator, *, on_open: Callable[[int], None],
                 on_add: Callable[[int], None]) -> None:
    with ui.card().classes('product-card w-full p-0 gap-0 rounded-2xl overflow-hidden no-shadow border border-[#ece2d4]') as card:
        card.props(f'id=product-{product.id}')
        with ui.element('div').classes('relative w-full bg-sand cursor-pointer') \
                .on('click', lambda: on_open(product.id)):
            ui.image(product_image(product.image)).props('ratio=1 fit=contain no-spinner') \
                .classes('product-photo w-full').style('padding: 14px')
            with ui.row().classes('absolute top-2 left-2 gap-1'):
                if product.promotion:
                    ui.badge(f'-{product.promotion.discount_percent}%', color='negative').props('rounded') \
                        .classes('font-bold')
                if product.availability != 'in_stock':
                    availability_badge(product, t)
        with ui.column().classes('w-full p-4 gap-2 grow'):
            ui.label(f'{product.brand} · {category}'.upper()).classes('text-[11px] tracking-wider text-muted')
            ui.label(t.product_name(product)) \
                .classes('line-clamp-2 font-semibold leading-snug cursor-pointer min-h-[2.6em]') \
                .on('click', lambda: on_open(product.id))
            price_block(product, t)
            ui.space()
            button = ui.button(t('card.add'), icon='add_shopping_cart', on_click=lambda: on_add(product.id)) \
                .props('unelevated rounded no-caps').classes('w-full mt-1')
            if not product.is_available:
                button.props('disable').set_text(t(f'availability.{product.availability}'))
