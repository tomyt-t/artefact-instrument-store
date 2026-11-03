"""Storefront page: wires the store data, the cart and the agent's UI events to the components."""
from __future__ import annotations

import os
from pathlib import Path

from nicegui import app, ui

from ..cart import Cart
from ..data import load_store
from ..i18n import DEFAULT_LANGUAGE, Translator
from . import theme
from .components.catalog import CatalogView, offers_strip
from .components.layout import SiteHeader, hero, perks, site_footer

STATIC_DIR = Path(__file__).resolve().parent / 'static'


@ui.page('/')
def index() -> None:
    ui.query('body').classes('bg-cream')
    store = load_store()
    t = Translator(app.storage.user.get('language', DEFAULT_LANGUAGE))
    ui.run_javascript(f'document.documentElement.lang = "{t.language}"')
    cart = Cart(store, app.storage.user.setdefault('cart', {}), t)

    def add_from_click(product_id: int) -> None:
        error = cart.add(product_id)
        if error:
            ui.notify(error, type='warning')
        else:
            ui.notify(t('card.added', name=t.product_name(store.products[product_id])), type='positive', icon='check')

    SiteHeader(t, on_search=lambda q: catalog.set_query(q))
    with ui.column().classes('w-full max-w-7xl mx-auto px-4 md:px-8 pt-6 pb-4 gap-12'):
        hero(t, on_browse=lambda: ui.run_javascript(
            'document.getElementById("catalogo")?.scrollIntoView({behavior: "smooth"})'))
        perks(t)
        offers_strip(store, t, on_add=add_from_click)
        catalog = CatalogView(store, t, on_add=add_from_click)
    site_footer(t)


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
