"""Floating chat (bottom-right) that renders the agent's event stream: text, tool status and UI actions."""
from __future__ import annotations

import logging
from collections.abc import Callable, MutableSequence

from nicegui import background_tasks, ui

from ...agent import Agent, AgentContext, ChatMessage
from ...agent.events import (AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts, Suggestions,
                             TextDelta, ToolStatus)
from ...agent.persona import ASSISTANT_NAME
from ...i18n import Translator
from ...policies import STORE
from ..router import UIEventRouter

log = logging.getLogger(__name__)

MARKDOWN_EXTRAS = ['fenced-code-blocks', 'tables', 'cuddled-lists']  # the model often leaves no blank line before a list
HISTORY_FOR_AGENT = 20  # previous messages sent with each turn
HISTORY_KEPT = 60  # messages persisted per visitor
TYPING = '<div class="typing py-1"><span></span><span></span><span></span></div>'
RECEIPT_ICONS = {AddToCart: 'add_shopping_cart', RemoveFromCart: 'remove_shopping_cart', OpenCart: 'shopping_cart',
                 ShowProducts: 'storefront', OpenProduct: 'visibility', ShowOrder: 'local_shipping'}


class ChatWidget:
    def __init__(self, router: UIEventRouter, history: MutableSequence[dict], *,
                 session_id: str, context: Callable[[], dict], agent: Agent, t: Translator) -> None:
        self.agent = agent
        self.t = t
        self.router = router
        self.history = history  # [{'role', 'content', 'receipts'}], persisted in app.storage.user
        self.session_id = session_id
        self.context = context  # extra AgentContext fields from the page: cart snapshot, product on screen
        self.busy = False
        self.unread = 0

        with ui.page_sticky('bottom-right', x_offset=20, y_offset=20).classes('z-[2000]'):
            with ui.column().classes('items-end gap-3'):
                self._build_panel()
                with ui.row().classes('items-center gap-3 no-wrap'):
                    self.teaser = ui.label(t('chat.teaser', name=ASSISTANT_NAME)) \
                        .classes('bg-white rounded-2xl px-4 py-2 text-sm shadow-lg cursor-pointer chat-enter') \
                        .on('click', self.open)
                    self.teaser.visible = False
                    with ui.button(icon='chat', on_click=self.toggle) \
                            .props('round unelevated size=lg color=primary').classes('chat-fab') as self.fab:
                        self.badge = ui.badge(color='negative').props('floating rounded')
                        self.badge.visible = False
        self._render_history()
        if not self.history:
            ui.timer(1.5, lambda: self.teaser.set_visibility(not self.panel.visible), once=True)

    def _build_panel(self) -> None:
        with ui.card().classes('chat-panel chat-enter p-0 gap-0 overflow-hidden no-wrap') \
                .classes('w-[min(400px,calc(100vw-40px))] h-[min(640px,calc(100vh-120px))]') as self.panel:
            with ui.row().classes('w-full items-center gap-3 px-4 py-3 bg-secondary text-white no-wrap'):
                ui.avatar('music_note', color='primary', text_color='white', size='42px')
                with ui.column().classes('gap-0 grow'):
                    ui.label(ASSISTANT_NAME).classes('font-display text-lg leading-tight')
                    with ui.row().classes('items-center gap-1'):
                        ui.element('span').classes('w-2 h-2 rounded-full bg-green-400')
                        ui.label(self.t('chat.role')).classes('text-xs opacity-80')
                ui.button(icon='restart_alt', on_click=self.reset).props('flat round dense color=white') \
                    .tooltip(self.t('chat.new'))
                ui.button(icon='expand_more', on_click=self.close).props('flat round dense color=white')
            # column-reverse keeps the view pinned to the newest message with no scroll calls
            with ui.element('div').classes('grow min-h-0 w-full bg-cream overflow-y-auto flex flex-col-reverse'):
                self.messages = ui.column().classes('w-full gap-3 px-3 py-2 mb-auto')
            with ui.row().classes('w-full items-end gap-2 px-3 pt-3 pb-1 bg-white no-wrap'):
                self.input = ui.textarea(placeholder=self.t('chat.placeholder')) \
                    .props('autogrow rows=1 outlined rounded dense maxlength=600 input-class=max-h-28') \
                    .classes('grow')
                self.input.on('keydown.enter.exact.prevent', self._send_input)
                self.send_button = ui.button(icon='send', on_click=self._send_input).props('round unelevated')
            ui.label(self.t('chat.disclaimer', phone=STORE.phone)) \
                .classes('w-full text-center text-[11px] text-muted pb-2 bg-white')
        self.panel.visible = False

    # --- opening and closing ---------------------------------------------------------------------------------

    def open(self) -> None:
        self.panel.visible = True
        self.teaser.visible = False
        self.unread = 0
        self.badge.visible = False
        self.fab.props('icon=close')
        self.input.run_method('focus')

    def close(self) -> None:
        self.panel.visible = False
        self.fab.props('icon=chat')

    def toggle(self) -> None:
        self.close() if self.panel.visible else self.open()

    def ask(self, text: str) -> None:
        """Opens the chat and sends `text` as if the customer typed it (used by 'Perguntar ao assistente')."""
        self.open()
        if self.busy:  # the question waits in the input instead of being lost
            self.input.value = text
            return
        background_tasks.create(self.send(text), context=ui.context.client)

    def reset(self) -> None:
        if self.busy:  # the answer being streamed still has to reach the history
            return
        self.history.clear()
        self._render_history()

    # --- conversation ----------------------------------------------------------------------------------------

    async def _send_input(self) -> None:
        text = self.input.value or ''
        if text.strip() and not self.busy:
            self.input.value = ''
            await self.send(text)

    async def send(self, text: str) -> None:
        text = text.encode('utf-8', 'ignore').decode().strip()  # a lone surrogate can't be stored or sent
        if not text or self.busy:
            return
        self._set_busy(True)
        try:
            await self._answer(text)
        finally:
            self._set_busy(False)

    async def _answer(self, text: str) -> None:
        self._clear_suggestions()
        with self.messages:
            ui.chat_message(text, sent=True).props('bg-color=primary text-color=white')
        previous = [ChatMessage(m['role'], m['content']) for m in self.history[-HISTORY_FOR_AGENT:]]
        self._remember({'role': 'user', 'content': text})
        bubble = _AssistantBubble(self.messages, live=True)

        answer, receipts, suggestions = '', [], ()
        try:
            context = AgentContext(session_id=self.session_id, history=previous, **self.context())
            async for event in self.agent.reply(text, context):
                match event:
                    case TextDelta():
                        answer += event.text
                        bubble.set_text(answer)
                    case ToolStatus():
                        bubble.set_status(event.label)
                    case Suggestions():
                        suggestions = event.options
                    case _:
                        for receipt in await self.router.dispatch(event):
                            receipts.append((RECEIPT_ICONS.get(type(event), 'bolt'), receipt))
                            bubble.add_receipt(*receipts[-1])
        except Exception:
            log.exception('agent failed')
            answer += ('\n\n' if answer else '') + self.t('chat.error', phone=STORE.phone)
            bubble.set_text(answer)
        bubble.finish()
        self._remember({'role': 'assistant', 'content': answer, 'receipts': receipts})
        self._show_suggestions(suggestions)
        if not self.panel.visible:
            self.unread += 1
            self.badge.set_text(str(self.unread))
            self.badge.visible = True

    def _render_history(self) -> None:
        self.messages.clear()
        _AssistantBubble(self.messages, self.t('chat.greeting', name=ASSISTANT_NAME))
        for message in self.history:
            if message['role'] == 'user':
                with self.messages:
                    ui.chat_message(message['content'], sent=True).props('bg-color=primary text-color=white')
            else:
                bubble = _AssistantBubble(self.messages, message['content'])
                for icon, receipt in message.get('receipts', []):
                    bubble.add_receipt(icon, receipt)
        self.suggestions = None
        self._show_suggestions(() if self.history else tuple(self.t(f'chat.starter.{n}') for n in (1, 2, 3, 4)))

    def _show_suggestions(self, options: tuple[str, ...]) -> None:
        """Quick replies always sit below the newest message; a new row is built instead of moving the old one,
        since moving an element and filling it in the same update froze the browser."""
        self._clear_suggestions()
        if options:
            with self.messages:
                self.suggestions = ui.row().classes('w-full gap-2 px-2')
                with self.suggestions:
                    for option in options:
                        # ask, not send: this button is deleted mid-answer and its context would go with it
                        ui.button(option, on_click=lambda o=option: self.ask(o)) \
                            .props('outline rounded no-caps dense color=primary').classes('bg-white px-3 text-[13px]')

    def _clear_suggestions(self) -> None:
        if self.suggestions is not None:
            self.suggestions.delete()
            self.suggestions = None

    def _remember(self, message: dict) -> None:
        self.history.append(message)
        del self.history[:-HISTORY_KEPT]

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.send_button.props('loading')
        else:
            self.send_button.props(remove='loading')


class _AssistantBubble:
    """One assistant answer: typing dots, then tool status, streamed markdown and action receipts."""

    def __init__(self, container: ui.column, content: str = '', *, live: bool = False) -> None:
        with container, ui.column().classes('w-full gap-1 items-start'):
            # a single child: Quasar renders every child of q-chat-message as a separate bubble
            with ui.chat_message().props('bg-color=white text-color=dark').classes('max-w-[92%]'), \
                    ui.column().classes('gap-1'):
                self.status = ui.row().classes('items-center gap-2 text-xs text-muted')
                self.typing = ui.html(TYPING) if live else None  # a stored empty answer must not look like typing
                self.markdown = ui.markdown(content, extras=MARKDOWN_EXTRAS).classes('chat-markdown text-[14px] leading-relaxed')
                self.markdown.visible = bool(content)
            self.receipts = ui.row().classes('gap-1 pl-3')

    def set_status(self, label: str) -> None:
        self.status.clear()
        with self.status:
            ui.spinner('dots', size='sm', color='primary')
            ui.label(label)

    def set_text(self, text: str) -> None:
        if self.typing:
            self.typing.delete()
            self.typing = None
        self.status.clear()
        self.markdown.set_content(text)
        self.markdown.visible = True

    def add_receipt(self, icon: str, text: str) -> None:
        with self.receipts:
            ui.chip(text, icon=icon).props('dense outline color=primary').classes('text-xs bg-white')

    def finish(self) -> None:
        self.status.clear()
        if self.typing:
            self.typing.delete()
            self.typing = None
