"""Chat with the assistant in the terminal: `uv run python -m emporio.cli`.

A scripted conversation (one customer message per line) can be replayed with a fixed clock:

    uv run python -m emporio.cli --script roteiro.txt --now 2026-11-02T10:00 --transcript conversa.md
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .agent.base import Agent, AgentContext, ChatMessage
from .agent.config import load_config
from .agent.events import AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts, Suggestions
from .agent.events import TextDelta, ToolStatus
from .agent.llm import create_agent
from .agent.persona import ASSISTANT_NAME
from .cart import Cart
from .data import load_store
from .i18n import DEFAULT_LANGUAGE, LANGUAGES, Translator

EXIT_WORDS = {'sair', 'exit', 'quit'}


class _ToolLog(logging.Handler):
    """Collects the agent's tool calls (name and arguments) for the transcript."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.calls: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == 'tool %s %s':
            self.calls.append(f'{record.args[0]} {record.args[1]}')


def _typed(t: Translator) -> Iterable[str]:
    interactive = sys.stdin.isatty()
    while True:
        try:
            yield input(f'\n{t("cli.you")}: ' if interactive else '')
        except (EOFError, KeyboardInterrupt):
            return


async def chat(agent: Agent, messages: Iterable[str], *, now: datetime | None, transcript: Path | None,
               echo: bool, pause: float = 0, language: str = DEFAULT_LANGUAGE) -> None:
    t = Translator(language)
    greeting = t('chat.greeting', name=ASSISTANT_NAME)
    store = load_store()
    cart = Cart(store, {})
    history = [ChatMessage('assistant', greeting)]  # what the storefront chat shows first
    verified: set[int] = set()
    tool_log = _ToolLog()
    logger = logging.getLogger('emporio.agent.llm')
    logger.addHandler(tool_log)
    logger.setLevel(logging.INFO)
    config = load_config()
    print(f'{ASSISTANT_NAME}: {greeting}')
    lines = [f'# {t("cli.title")}', '',
             f'_{config.model if config else t("cli.no_model")}'
             + (f' · {t("cli.simulated")}: {t(f"weekday.{now.weekday()}")}, {t.date(now.date())}, {now:%H:%M}' if now else '')
             + '_', '', f'**{ASSISTANT_NAME}:** {greeting}', '']
    for message in messages:
        message = message.lstrip('﻿').strip()
        if not message:
            continue
        if message.lower() in EXIT_WORDS:
            break
        if echo:
            print(f'\n{t("cli.you")}: {message}')
        print(f'{ASSISTANT_NAME}: ', end='', flush=True)
        context = AgentContext(session_id='cli', history=list(history), cart=cart.snapshot(), now=now,
                               verified_orders=verified, language=t.language)
        answer, notes = '', []
        tool_log.calls.clear()
        async for event in agent.reply(message, context):
            match event:
                case TextDelta():
                    answer += event.text
                    print(event.text, end='', flush=True)
                case ToolStatus():
                    pass
                case Suggestions():
                    notes.append(f'{t("cli.suggestions")}: ' + ' | '.join(event.options))
                case _:
                    notes.append(_apply(event, cart, store, t))
        print()
        for note in notes:
            print(f'  [{note}]')
        history += [ChatMessage('user', message), ChatMessage('assistant', answer)]
        lines += [f'**{t("cli.customer")}:** {message}', '', f'**{ASSISTANT_NAME}:** {answer}', '']
        if tool_log.calls or notes:
            lines += [f'> `{call}`  ' for call in tool_log.calls] + [f'> {note}  ' for note in notes] + ['']
        await asyncio.sleep(pause)
    logger.removeHandler(tool_log)
    if transcript:
        transcript.write_text('\n'.join(lines), encoding='utf-8')
        print(f'\n{t("cli.saved", path=transcript)}')


def _apply(event, cart: Cart, store, t: Translator) -> str:
    """What the storefront would do with a UI event, described in one line (and applied to the CLI cart)."""
    match event:
        case ShowProducts():
            return t('cli.shown', count=len(event.product_ids), title=event.title)
        case OpenProduct():
            return t('cli.opened', name=t.product_name(store.products[event.product_id]))
        case AddToCart():
            error = cart.add(event.product_id, event.quantity)
            return error or t('cli.cart_add', quantity=event.quantity, name=t.product_name(store.products[event.product_id]))
        case RemoveFromCart():
            cart.remove(event.product_id)
            return t('cli.cart_remove', name=t.product_name(store.products[event.product_id]))
        case OpenCart():
            return t('cli.cart_open', count=cart.count)
        case ShowOrder():
            return t('cli.order_open', id=event.order_id)
    return f'evento: {event}'


def main(argv: list[str] | None = None) -> None:
    # the Windows console and pipes default to a legacy code page; 'utf-8-sig' also drops the BOM PowerShell adds
    for stream, encoding in ((sys.stdin, 'utf-8-sig'), (sys.stdout, 'utf-8'), (sys.stderr, 'utf-8')):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding=encoding)
    parser = argparse.ArgumentParser(prog='python -m emporio.cli',
                                     description='Talk to the Empório da Música assistant in the terminal.')
    parser.add_argument('--script', type=Path, help='file with one customer message per line')
    parser.add_argument('--now', type=datetime.fromisoformat,
                        help='fixed date and time in Campo Grande, e.g. 2026-11-02T10:00')
    parser.add_argument('--transcript', type=Path, help='saves the conversation to a markdown file')
    parser.add_argument('--pause', type=float, default=0, help='seconds between messages, to respect the quota')
    parser.add_argument('--language', choices=LANGUAGES, default=DEFAULT_LANGUAGE,
                        help='language of the greeting and the labels; the agent answers in the customer\'s language')
    args = parser.parse_args(argv)
    if args.script and not args.script.is_file():
        parser.error(f'script not found: {args.script}')
    if args.transcript and not args.transcript.resolve().parent.is_dir():  # better now than after the whole conversation
        parser.error(f'transcript folder not found: {args.transcript.parent}')
    if args.script:
        messages, echo = args.script.read_text(encoding='utf-8-sig').splitlines(), True
    else:
        messages, echo = _typed(Translator(args.language)), not sys.stdin.isatty()
        if not echo:
            print(Translator(args.language)('cli.welcome'))
    errors = logging.StreamHandler(sys.stderr)  # a wrong key or model name must not fail silently
    errors.setLevel(logging.WARNING)
    errors.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    logging.getLogger('emporio').addHandler(errors)
    try:
        asyncio.run(chat(create_agent(), messages, now=args.now, transcript=args.transcript, echo=echo,
                         pause=args.pause, language=args.language))
    finally:
        logging.getLogger('emporio').removeHandler(errors)


if __name__ == '__main__':
    main()
