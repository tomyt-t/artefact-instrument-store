"""The storefront with a scripted agent: what each event does on the page, driven by NiceGUI's simulated user."""
import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from emporio.agent.config import SETUP
from emporio.agent.events import (AddToCart, OpenCart, OpenProduct, ShowOrder, ShowProducts, Suggestions, TextDelta,
                                  ToolStatus)
from emporio.agent.llm import SetupAgent
from emporio.i18n import STORE_LANGUAGE, Translator
from emporio.web import app as storefront
from emporio.web.components.chat import TYPING, ChatWidget

QUESTION = Translator(STORE_LANGUAGE)('chat.starter.1')


class ScriptedAgent:
    def __init__(self, *events, verify=(), gate=None, error=None):
        self.events, self.verify, self.gate, self.error = events, verify, gate, error
        self.contexts = []

    async def reply(self, message, context):
        self.contexts.append(context)
        context.verified_orders.update(self.verify)
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        for event in self.events:
            yield event


@pytest.fixture
def page(monkeypatch):
    """Opens the storefront with `agent` and gives back the page's chat widget."""
    widgets = []

    class Recorded(ChatWidget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            widgets.append(self)

    monkeypatch.setattr(storefront, 'ChatWidget', Recorded)

    async def page(user: User, agent, language='pt-BR'):  # None keeps the site's real default
        monkeypatch.setattr(storefront, '_agent', lambda: agent)
        if language:
            monkeypatch.setattr(storefront, 'DEFAULT_LANGUAGE', language)
        ui.page('/')(storefront.index)  # the simulation clears the routes; the module was imported only once
        await user.open('/')
        return widgets[-1]
    return page


async def wait_until_idle(chat):
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not chat.busy:
            return
    raise AssertionError('the chat stayed busy')


async def ask(user, chat):
    with user:
        chat.open()
    user.find(QUESTION).click()
    await wait_until_idle(chat)


async def send(user, chat, text):
    with user:
        await chat.send(text)


async def test_the_page_opens_in_english_and_the_selector_changes_the_language(user: User, page):
    chat = await page(user, ScriptedAgent(TextDelta('Hi')), language=None)
    with user:
        chat.open()
    await user.should_see('Our instruments')
    await user.should_see('Which acoustic guitars in stock cost up to R$1000?')
    await user.should_see('Acoustic guitars')
    await user.should_not_see('Nossos instrumentos')
    user.find(marker='language-fr').click()
    await user.open('/')
    await user.should_see('Nos instruments')
    await user.should_see('Guitares acoustiques')
    user.find(marker='language-pt-BR').click()
    await user.open('/')
    await user.should_see('Nossos instrumentos')


async def test_the_page_tells_the_agent_its_language_and_translates_the_receipts(user: User, page):
    agent = ScriptedAgent(AddToCart(112, 1), TextDelta('Done!'))
    chat = await page(user, agent, language='en')
    with user:
        chat.open()
    await send(user, chat, 'add it')
    await user.should_see('In your cart: Tagima TW-7 7-String Steel Natural')
    assert agent.contexts[-1].language == 'en'


async def test_events_reach_the_page_and_the_history(user: User, page):
    agent = ScriptedAgent(ToolStatus('buscar_produtos', 'Consultando o catálogo…'),
                          ShowProducts((81,), 'Violões até R$ 1.000,00'), AddToCart(81, 1),
                          TextDelta('Coloquei o '), TextDelta('C40 no carrinho!'), Suggestions(('Ver o carrinho',)))
    chat = await page(user, agent)
    await ask(user, chat)
    await user.should_see('Coloquei o C40 no carrinho!')
    await user.should_see('No carrinho: Yamaha C40 Nylon Natural')
    await user.should_see('Ver o carrinho')
    assert [m['role'] for m in chat.history] == ['user', 'assistant']
    assert chat.history[1]['content'] == 'Coloquei o C40 no carrinho!'


async def test_second_turn_sees_the_cart_and_the_history(user: User, page):
    agent = ScriptedAgent(AddToCart(81, 1), TextDelta('Pronto.'))
    chat = await page(user, agent)
    await send(user, chat, 'coloca o C40')
    agent.events = (TextDelta('Pronto.'),)
    await send(user, chat, 'quanto fica?')
    second = agent.contexts[1]
    assert [item['product_id'] for item in second.cart['items']] == [81]
    assert [m.role for m in second.history] == ['user', 'assistant']


async def test_order_opens_only_after_the_agent_verified_it(user: User, page):
    chat = await page(user, ScriptedAgent(ShowOrder(8), TextDelta('Aqui está.')))
    await send(user, chat, 'me mostra o pedido 8')
    await user.should_not_see('Pedido #8')
    assert chat.history[-1].get('receipts') == []
    chat.agent = ScriptedAgent(ShowOrder(8), TextDelta('Aqui está.'), verify={8})
    await send(user, chat, 'pedido 8, anacarol.ferreira@coldmail.com')
    await user.should_see('Pedido #8')
    await user.should_see('28/02/2026 · atrasada')


async def test_a_new_conversation_forgets_the_verified_orders(user: User, page):
    agent = ScriptedAgent(ShowOrder(8), TextDelta('Aqui está.'), verify={8})
    chat = await page(user, agent)
    await send(user, chat, 'pedido 8, anacarol.ferreira@coldmail.com')
    assert agent.contexts[-1].verified_orders == {8}
    with user:
        chat.reset()
    assert agent.contexts[-1].verified_orders == set()
    chat.agent = ScriptedAgent(ShowOrder(8), TextDelta('Aqui está.'))
    await send(user, chat, 'e o pedido 8?')
    assert chat.history[-1].get('receipts') == []


async def test_broken_unicode_from_the_browser_does_not_break_the_chat(user: User, page):
    agent = ScriptedAgent(TextDelta('Oi!'))
    chat = await page(user, agent)
    await send(user, chat, 'oi \ud83d')
    assert chat.history[0]['content'] == 'oi' and chat.history[1]['content'] == 'Oi!'


async def test_conflicting_product_details_are_not_shown(user: User, page):
    chat = await page(user, ScriptedAgent(OpenProduct(135), TextDelta('Abri.')))
    await send(user, chat, 'me mostra o Music Man')
    await user.should_see('Especificações a confirmar com a equipe')
    await user.should_not_see('Jazz Bass')  # 135 is a Music Man described as a Fender Jazz Bass


async def test_the_cart_shows_the_original_price_and_the_discount(user: User, page):
    chat = await page(user, ScriptedAgent(AddToCart(94, 2), OpenCart(), TextDelta('Pronto.')))
    await send(user, chat, 'coloca dois Taylor 110e')
    await user.should_see('8% de desconto')
    await user.should_see('R$ 11.998,00')  # two at R$ 5.999,00


async def test_reset_waits_for_the_answer_and_a_question_waits_in_the_input(user: User, page):
    gate = asyncio.Event()
    chat = await page(user, ScriptedAgent(TextDelta('Oi!'), gate=gate))
    with user:
        chat.open()
    user.find(QUESTION).click()
    await asyncio.sleep(0.05)
    assert chat.busy
    with user:
        chat.reset()
    assert chat.history[0]['content'] == QUESTION
    with user:
        chat.ask('e o frete?')
    assert chat.input.value == 'e o frete?'
    gate.set()
    await wait_until_idle(chat)
    assert [m['content'] for m in chat.history] == [QUESTION, 'Oi!']
    with user:
        chat.reset()
    assert chat.history == []


async def test_agent_failure_is_explained_and_releases_the_chat(user: User, page, caplog):
    chat = await page(user, ScriptedAgent(error=RuntimeError('boom')))
    await send(user, chat, 'oi')
    assert not chat.busy and 'problema técnico' in chat.history[-1]['content']
    assert 'agent failed' in caplog.text
    caplog.clear()  # logged on purpose; the `user` fixture fails on unexpected ERROR logs


async def test_stored_empty_answer_does_not_look_like_typing(user: User, page):
    chat = await page(user, ScriptedAgent(TextDelta('Oi')))
    chat.history.append({'role': 'assistant', 'content': ''})
    with user:
        chat._render_history()
    assert not [e for e in chat.messages.descendants() if isinstance(e, ui.html) and e.content == TYPING]


async def test_without_a_key_the_chat_explains_the_setup(user: User, page):
    chat = await page(user, SetupAgent())
    await send(user, chat, 'oi')
    assert chat.history[-1]['content'] == SETUP
