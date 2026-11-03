"""Storefront page: wires the store data, the cart and the agent's UI events to the components."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from nicegui import app, ui

from ..agent import Agent
from ..agent.events import AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts
from ..agent.llm import create_agent
from ..cart import Cart
from ..data import load_store
from ..i18n import DEFAULT_LANGUAGE, Translator
from . import theme
from .components.cart_drawer import CartDrawer
from .components.catalog import CatalogView, offers_strip
from .components.chat import ChatWidget
from .components.layout import SiteHeader, hero, perks, site_footer
from .components.order_dialog import OrderDialog
from .components.product_dialog import ProductDialog
from .router import UIEventRouter

STATIC_DIR = Path(__file__).resolve().parent / 'static'


@lru_cache(maxsize=1)
def _agent() -> Agent:
    """One per process: it counts the failed order lookups of every visitor. With no API key it explains the setup."""
    return create_agent()


@ui.page('/')
def index() -> None:
    ui.query('body').classes('bg-cream')
    store = load_store()
    t = Translator(app.storage.user.get('language', DEFAULT_LANGUAGE))
    ui.run_javascript(f'document.documentElement.lang = "{t.language}"')
    cart = Cart(store, app.storage.user.setdefault('cart', {}), t)
    router = UIEventRouter()
    verified_orders: set[int] = set()  # orders whose e-mail the customer confirmed to the agent on this page

    def show_order(action: ShowOrder) -> str | None:
        """Only orders verified by the agent's tool: a model's word is not enough to show someone's order (LGPD)."""
        return orders.open(action.order_id, seamless=True) if action.order_id in verified_orders else None

    def forget_orders() -> None:
        """A new conversation asks for the e-mail again: a shared computer must not keep someone's order open."""
        verified_orders.clear()
        orders.dialog.close()

    def add_from_click(product_id: int) -> None:
        error = cart.add(product_id)
        if error:
            ui.notify(error, type='warning')
        else:
            ui.notify(t('card.added', name=t.product_name(store.products[product_id])), type='positive', icon='check')

    def add_from_agent(action: AddToCart) -> str:
        error = cart.add(action.product_id, action.quantity)
        return error or t('cart.added', name=t.product_name(store.products[action.product_id]))

    def remove_from_agent(action: RemoveFromCart) -> str | None:
        if product := store.products.get(action.product_id):
            cart.remove(product.id)
            return t('cart.removed', name=t.product_name(product))
        return None

    header = SiteHeader(t, on_search=lambda q: catalog.set_query(q), on_cart=lambda: cart_drawer.open(),
                        on_track=lambda: orders.lookup.open())
    with ui.column().classes('w-full max-w-7xl mx-auto px-4 md:px-8 pt-6 pb-4 gap-12'):
        hero(t, on_browse=lambda: ui.run_javascript(
            'document.getElementById("catalogo")?.scrollIntoView({behavior: "smooth"})'),
            on_chat=lambda: chat.open())
        perks(t)
        offers_strip(store, t, on_open=lambda pid: product_dialog.open(pid), on_add=add_from_click)
        catalog = CatalogView(store, t, on_open=lambda pid: product_dialog.open(pid), on_add=add_from_click)
    site_footer(t)

    cart_drawer = CartDrawer(cart, t, on_ask=lambda text: chat.ask(text))
    product_dialog = ProductDialog(store, t, on_add=add_from_click, on_ask=lambda text: chat.ask(text))
    orders = OrderDialog(store, t)
    chat = ChatWidget(router, app.storage.user.setdefault('chat', []),
                      session_id=app.storage.browser['id'], agent=_agent(), t=t, on_reset=forget_orders,
                      context=lambda: {'cart': cart.snapshot(), 'viewing_product_id': product_dialog.viewing_product_id,
                                       'verified_orders': verified_orders, 'language': t.language})

    header.set_cart_count(cart.count)
    cart.on_change(lambda: header.set_cart_count(cart.count, bump=True))

    def close_panels(_action) -> None:
        """The agent shows one thing at a time: a new view replaces the panel it opened before."""
        product_dialog.dialog.close()
        orders.dialog.close()

    # every UI action the agent can emit has a listener here
    for view in (ShowProducts, OpenProduct, OpenCart, ShowOrder):
        router.on(view, close_panels)
    router.on(ShowProducts, catalog.show_selection)
    router.on(OpenProduct, lambda action: product_dialog.open(action.product_id, seamless=True))
    router.on(AddToCart, add_from_agent)
    router.on(RemoveFromCart, remove_from_agent)
    router.on(OpenCart, cart_drawer.open)
    router.on(ShowOrder, show_order)


def run() -> None:
    theme.setup()
    app.add_static_files('/static', STATIC_DIR)
    ui.run(
        title='Empório da Música',
        favicon='🎸',
        language='en-US',
        storage_secret=os.environ.get('EMPORIO_STORAGE_SECRET', 'emporio-dev-secret'),
        port=int(os.environ.get('PORT', 8080)),
        reload=os.environ.get('EMPORIO_RELOAD') == '1',
        show=False,
    )
