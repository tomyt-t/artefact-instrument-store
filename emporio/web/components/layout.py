"""Header, language selector, hero, perks and footer of the storefront."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from nicegui import app, ui

from ... import policies
from ...format import brl
from ...i18n import Translator
from ...policies import STORE
from ..theme import product_image

CREDITS = Path(__file__).resolve().parent.parent / 'static' / 'products' / 'IMAGE_CREDITS.md'
LANGUAGE_CHOICES = {'en': ('EN', 'us'), 'fr': ('FR', 'fr'), 'pt-BR': ('PT-BR', 'br')}  # language -> (label, flag file)


def store_status(t: Translator, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the store is open right now, and a short status in the page's language."""
    now = now or datetime.now(policies.TIMEZONE)
    hours = policies.hours_on(now.date())
    if hours and hours[0] <= now.hour < hours[1]:
        return True, t('status.open', hour=t.hour(hours[1]))
    opening = policies.next_opening(now)
    if opening is None:
        return False, t('status.closed')
    days_ahead = (opening.date() - now.date()).days
    when = (t('status.today') if days_ahead == 0 else t('status.tomorrow') if days_ahead == 1
            else t(f'weekday.{opening.weekday()}'))
    return False, t('status.closed_until', when=when, hour=t.hour(opening.hour))


def _flag(language: str, size: str) -> None:
    label, flag = LANGUAGE_CHOICES[language]
    ui.element('img').props(f'src=/static/flags/{flag}.svg alt={label}').classes(f'{size} rounded-[3px]')


def language_selector(t: Translator) -> None:
    def choose(language: str) -> None:
        app.storage.user['language'] = language
        ui.navigate.reload()

    with ui.button().props('flat rounded no-caps color=secondary').classes('shrink-0 px-2') \
            .tooltip(t('language.label')):
        with ui.row().classes('items-center gap-2 no-wrap'):
            _flag(t.language, 'w-8 h-[22px]')
            ui.label(LANGUAGE_CHOICES[t.language][0]).classes('text-sm font-medium max-sm:hidden')
            ui.icon('expand_more', size='sm')
        with ui.menu():
            for language, (label, _) in LANGUAGE_CHOICES.items():
                with ui.menu_item(on_click=lambda chosen=language: choose(chosen)).mark(f'language-{language}'):
                    with ui.row().classes('items-center gap-3 no-wrap'):
                        _flag(language, 'w-7 h-5')
                        ui.label(label).classes('text-sm font-medium')


class SiteHeader:
    def __init__(self, t: Translator, *, on_search: Callable[[str], None], on_cart: Callable[[], None]) -> None:
        with ui.header().classes('bg-cream text-ink p-0 gap-0 border-b border-[#ece2d4]'):
            with ui.row().classes('w-full justify-center gap-6 bg-secondary text-white text-xs py-1.5 px-4 no-wrap'):
                ui.label(t('header.pix_off', percent=f'{policies.PIX_DISCOUNT:.0%}')).classes('max-sm:hidden')
                ui.label(t('header.installments')).classes('max-sm:hidden')
                ui.label(t('header.free_shipping', amount=brl(policies.FREE_SHIPPING_ABOVE)))
            with ui.row().classes('w-full max-w-7xl mx-auto items-center gap-4 px-4 md:px-8 py-3 no-wrap'):
                with ui.row().classes('items-center gap-3 no-wrap cursor-pointer') \
                        .on('click', lambda: ui.navigate.to('/')):
                    ui.avatar('music_note', color='primary', text_color='white', size='44px')
                    with ui.column().classes('gap-0 max-sm:hidden'):
                        ui.label(STORE.name).classes('font-display text-2xl leading-none')
                        ui.label(t('store.slogan')).classes('text-xs text-muted')
                with ui.input(placeholder=t('header.search'), on_change=lambda e: on_search(e.value)) \
                        .props('outlined rounded dense clearable debounce=300 bg-color=white') \
                        .classes('grow max-w-xl mx-auto') as search:
                    with search.add_slot('prepend'):
                        ui.icon('search', color='grey-7')
                is_open, status = store_status(t)
                with ui.row().classes('items-center gap-2 text-sm max-lg:hidden'):
                    ui.element('span').classes('w-2.5 h-2.5 rounded-full') \
                        .classes('bg-green-500' if is_open else 'bg-grey-5')
                    ui.label(status).classes('text-muted')
                language_selector(t)
                with ui.button(icon='shopping_bag', on_click=on_cart) \
                        .props('flat round size=lg color=secondary') as self.cart_button:
                    self.cart_badge = ui.badge('0', color='primary').props('floating rounded')

    def set_cart_count(self, count: int, *, bump: bool = False) -> None:
        self.cart_badge.set_text(str(count))
        self.cart_badge.visible = count > 0
        if bump:
            self.cart_badge.classes(remove='bump')
            ui.timer(0.05, lambda: self.cart_badge.classes('bump'), once=True)


def hero(t: Translator, *, on_browse: Callable[[], None]) -> None:
    with ui.row().classes('w-full items-center rounded-[32px] bg-sand overflow-hidden no-wrap max-md:flex-col') \
            .classes('px-8 md:px-14 py-10 gap-8'):
        with ui.column().classes('gap-5 md:w-1/2'):
            ui.label(t('hero.since', year=STORE.founded)).classes('text-sm tracking-wider uppercase text-primary')
            ui.label(t('hero.title')).classes('font-display text-4xl md:text-5xl leading-[1.05]')
            ui.label(t('hero.subtitle')).classes('text-lg text-muted max-w-lg')
            with ui.row().classes('gap-3 no-wrap max-sm:flex-wrap'):
                ui.button(t('hero.browse'), icon='arrow_downward', on_click=on_browse) \
                    .props('unelevated rounded no-caps size=md')
        with ui.element('div').classes('relative md:w-1/2 w-full h-[340px] md:h-[400px]'):
            for slug, position in (('violao-eletroacustico', 'left-[2%] top-[8%] w-[46%] -rotate-12'),
                                   ('guitarra-eletrica', 'left-[30%] top-0 w-[44%] rotate-6'),
                                   ('ukulele', 'right-0 bottom-[2%] w-[44%] -rotate-6')):
                ui.image(product_image(slug)).props('fit=contain no-spinner') \
                    .classes(f'hero-photo absolute aspect-square {position}')


def perks(t: Translator) -> None:
    percent = f'{policies.PIX_DISCOUNT:.0%}'
    items = [
        ('pix', t('perks.pix_title', percent=percent), t('perks.pix_text')),
        ('credit_card', t('perks.installments_title'), t('perks.installments_text')),
        ('local_shipping', t('perks.shipping_title'), t('perks.shipping_text', amount=brl(policies.FREE_SHIPPING_ABOVE))),
        ('autorenew', t('perks.return_title', days=policies.RETURN_WINDOW_DAYS), t('perks.return_text')),
    ]
    with ui.element('div').classes('w-full grid grid-cols-2 lg:grid-cols-4 gap-4'):
        for icon, title, text in items:
            with ui.row().classes('items-center gap-3 no-wrap rounded-2xl bg-white border border-[#ece2d4] p-4'):
                ui.icon(icon, color='primary', size='md')
                with ui.column().classes('gap-0'):
                    ui.label(title).classes('font-semibold')
                    ui.label(text).classes('text-xs text-muted')


def _credits(t: Translator) -> str:
    """The attribution table comes from IMAGE_CREDITS.md, next to the images; only its wording is translated."""
    rows = [line.replace('Public domain', t('credits.public_domain'))
            for line in CREDITS.read_text(encoding='utf-8').splitlines() if line.startswith('| `')]
    return '\n'.join([f'### {t("credits.heading")}', '', t('credits.intro'), '',
                      f'| {t("credits.file")} | {t("credits.original")} | {t("credits.author")} | {t("credits.license")} |',
                      '| --- | --- | --- | --- |', *rows])


def site_footer(t: Translator) -> None:
    with ui.dialog() as credits, ui.card().classes('max-w-2xl rounded-3xl p-6'):
        ui.markdown(_credits(t)).classes('text-sm')
    with ui.element('footer').classes('w-full bg-secondary text-white mt-8'):
        with ui.element('div').classes('max-w-7xl mx-auto px-4 md:px-8 py-12 grid grid-cols-1 md:grid-cols-4 gap-8'):
            with ui.column().classes('gap-2'):
                ui.label(STORE.name).classes('font-display text-2xl')
                ui.label(t('store.slogan')).classes('opacity-70')
                ui.label(t('footer.exclusive')).classes('text-sm opacity-70')
            with ui.column().classes('gap-1 text-sm'):
                ui.label(t('footer.visit')).classes('font-semibold text-base mb-1')
                ui.label(STORE.street).classes('opacity-80')
                ui.label(STORE.city).classes('opacity-80')
            with ui.column().classes('gap-1 text-sm'):
                ui.label(t('footer.hours')).classes('font-semibold text-base mb-1')
                ui.label(t('footer.weekdays')).classes('opacity-80')
                ui.label(t('footer.saturday')).classes('opacity-80')
                ui.label(t('footer.closed')).classes('opacity-80')
            with ui.column().classes('gap-1 text-sm'):
                ui.label(t('footer.service')).classes('font-semibold text-base mb-1')
                ui.label(t('footer.phone', phone=STORE.phone)).classes('opacity-80')
                ui.label(STORE.email).classes('opacity-80')
                ui.label(t('footer.payments')).classes('opacity-80')
        with ui.row().classes('max-w-7xl mx-auto px-4 md:px-8 pb-8 w-full justify-between text-xs opacity-60'):
            ui.label(t('footer.prototype'))
            ui.link(t('footer.credits'), '#').classes('text-white') \
                .on('click.prevent', credits.open)
