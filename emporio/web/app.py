"""Storefront page: wires the store data, the cart and the agent's UI events to the components."""
from __future__ import annotations

import os
from pathlib import Path

from nicegui import app, ui

from ..i18n import DEFAULT_LANGUAGE, Translator
from . import theme
from .components.layout import SiteHeader, hero, perks, site_footer

STATIC_DIR = Path(__file__).resolve().parent / 'static'


@ui.page('/')
def index() -> None:
    ui.query('body').classes('bg-cream')
    t = Translator(app.storage.user.get('language', DEFAULT_LANGUAGE))
    ui.run_javascript(f'document.documentElement.lang = "{t.language}"')

    SiteHeader(t)
    with ui.column().classes('w-full max-w-7xl mx-auto px-4 md:px-8 pt-6 pb-4 gap-12'):
        hero(t, on_browse=lambda: ui.run_javascript(
            'document.getElementById("catalogo")?.scrollIntoView({behavior: "smooth"})'))
        perks(t)
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
