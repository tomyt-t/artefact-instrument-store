"""The 'My order' form is the only thing between a visitor and someone else's order on that path."""
from nicegui import ui
from nicegui.testing import User

from emporio.i18n import Translator
from emporio.web import app as storefront

t = Translator('en')


async def look_up(user: User, email: str) -> None:
    user.find(t('order.email')).clear().type(email)
    user.find(t('order.search')).click()


async def test_the_form_only_opens_an_order_with_the_owner_email(user: User, monkeypatch):
    monkeypatch.setattr(storefront, 'DEFAULT_LANGUAGE', 'en')
    monkeypatch.setattr(storefront, '_agent', lambda: None)  # the form does not go through the agent
    ui.page('/')(storefront.index)  # the simulation clears the routes; the module was imported only once
    await user.open('/')
    user.find(t('header.my_order')).click()
    user.find(t('order.number')).type('8')

    for wrong in ('lucas.mendes@jmail.com', ''):  # another customer's e-mail, then none at all
        await look_up(user, wrong)
        await user.should_see(t('order.not_found'))
        await user.should_not_see(t('order.title', id=8))

    await look_up(user, 'anacarol.ferreira@coldmail.com')
    await user.should_see(t('order.title', id=8))
