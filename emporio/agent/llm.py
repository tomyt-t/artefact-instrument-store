"""The customer-service agent: a language model in a tool-calling loop over the store's tools.

Each turn streams the model's answer. When the model asks for tools, they run (`tools.Toolbox`), their storefront
events go to the interface right away, and their results go back to the model, up to MAX_TOOL_ROUNDS times.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import openai

from ..data import StoreData, load_store
from ..i18n import STORE_LANGUAGE, Translator
from ..policies import STORE
from .base import Agent, AgentContext
from .config import LLMConfig, load_config
from .events import AddToCart, AgentEvent, RemoveFromCart, Suggestions, TextDelta, ToolStatus
from .history import to_messages
from .language import ANSWER_IN, answer_language
from .prompt import system_prompt
from .tools import OrderGuard, Toolbox

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5  # then the model must answer with what it has
MAX_CALLS_PER_ROUND = 10  # more than the agent ever needs; each extra call only gets an error back
TURN_SECONDS = 120  # a stream that never ends must not hold the chat forever
ATTEMPTS = 3  # per model call, for rate limits and transient errors
MAX_RETRY_WAIT = 20.0  # seconds; a longer wait means the quota is gone for now
PROVIDER_FIELDS = ('extra_content',)  # Gemini puts its thought signature here; it must go back unchanged
_store_texts = Translator(STORE_LANGUAGE)  # the fixed answers below, in the language the CLI and the evals use
BUSY = _store_texts('agent.busy', phone=STORE.phone)
UNAVAILABLE = _store_texts('agent.unavailable', phone=STORE.phone)
NO_ANSWER = _store_texts('agent.no_answer')
CART_ALREADY_CHANGED = _store_texts('agent.cart_changed')


class LLMAgent:
    def __init__(self, config: LLMConfig, *, store: StoreData | None = None,
                 client: openai.AsyncOpenAI | None = None) -> None:
        self.config = config
        self.store = store or load_store()
        self.client = client or openai.AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                                                   max_retries=0, timeout=60)
        self.guard = OrderGuard()  # shared by every conversation of this process

    async def reply(self, message: str, context: AgentContext) -> AsyncIterator[AgentEvent]:
        toolbox = Toolbox(self.store, context, self.guard)
        language = answer_language(message, context.language)
        messages = [{'role': 'system', 'content': system_prompt(toolbox.now, context, self.store, language)},
                    *to_messages(context.history, message)]
        t = Translator(context.language)
        written, suggestions, cart_changed = '', (), False
        deadline = time.monotonic() + TURN_SECONDS

        def fallback(text: str) -> TextDelta:
            """What to say when the model fails, without making the customer repeat what already happened."""
            return TextDelta(('\n\n' if written.strip() else '') + text
                             + (f' {t("agent.cart_changed")}' if cart_changed else ''))

        for round_number in range(MAX_TOOL_ROUNDS + 1):
            step = _Step()
            try:
                stream = await self._complete(messages, toolbox, last_round=round_number == MAX_TOOL_ROUNDS)
                async for chunk in stream:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f'turn took more than {TURN_SECONDS} s')
                    first_text = not step.text
                    if text := step.feed(chunk):
                        if first_text and written and not written.endswith('\n'):
                            yield TextDelta('\n\n')  # a new round continues the answer in a new paragraph
                            written += '\n\n'
                        written += text
                        yield TextDelta(text)
            except openai.RateLimitError:
                yield fallback(t('agent.busy', phone=STORE.phone))
                return
            except Exception:  # an outage or anything odd in the stream: the customer still gets an answer
                log.exception('model call failed')
                yield fallback(t('agent.unavailable', phone=STORE.phone))
                return
            if not step.calls or round_number == MAX_TOOL_ROUNDS:  # a provider may ignore tool_choice='none'
                break
            messages.append(step.assistant_message())
            for number, call in enumerate(step.calls):
                if number >= MAX_CALLS_PER_ROUND:  # every call must get an answer, but not thousands of runs
                    messages.append({'role': 'tool', 'tool_call_id': call.id,
                                     'content': json.dumps({'erro': 'Chamadas demais de uma vez.'}, ensure_ascii=False)})
                    continue
                tool = toolbox.tools.get(call.name)
                yield ToolStatus(call.name, t(f'tool.{call.name}' if tool else 'tool.unknown'))
                log.info('tool %s %s', call.name, call.arguments)
                result = toolbox.call(call.name, call.arguments)
                for event in result.events:
                    cart_changed = cart_changed or isinstance(event, AddToCart | RemoveFromCart)
                    yield event
                suggestions = result.suggestions
                data = result.data if language == STORE_LANGUAGE else {**result.data, 'idioma': ANSWER_IN[language]}
                messages.append({'role': 'tool', 'tool_call_id': call.id,
                                 'content': json.dumps(data, ensure_ascii=False)})
        if not step.text.strip():  # the round that ended the turn said nothing, e.g. only 'vou conferir' before
            yield fallback(t('agent.no_answer'))
        if suggestions:
            yield Suggestions(suggestions)

    async def _complete(self, messages: list[dict], toolbox: Toolbox, *, last_round: bool):
        request = {'model': self.config.model, 'messages': messages, 'stream': True, 'tools': toolbox.schemas(),
                   'tool_choice': 'none' if last_round else 'auto'}
        if self.config.reasoning_effort:
            request['reasoning_effort'] = self.config.reasoning_effort
        if self.config.temperature is not None:
            request['temperature'] = self.config.temperature
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await self.client.chat.completions.create(**request)
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as error:
                wait = _retry_after(error, attempt)
                if attempt == ATTEMPTS or wait > MAX_RETRY_WAIT:
                    raise
                log.warning('model call failed (%s), retrying in %.1fs', type(error).__name__, wait)
                await asyncio.sleep(wait)
        raise AssertionError('unreachable')


class SetupAgent:
    """Answers every message with how to configure the model, so a fresh clone still runs end to end."""

    async def reply(self, message: str, context: AgentContext) -> AsyncIterator[AgentEvent]:
        yield TextDelta(Translator(context.language)('agent.setup', phone=STORE.phone))


def create_agent() -> Agent:
    config = load_config()
    return LLMAgent(config) if config else SetupAgent()


@dataclass
class _ToolCall:
    id: str
    name: str = ''
    arguments: str = ''
    extra: dict = field(default_factory=dict)  # provider fields, e.g. Gemini's thought signature, sent back as is


class _Step:
    """One streamed model response: its text and the tool calls it asks for."""

    def __init__(self) -> None:
        self.text = ''
        self.calls: list[_ToolCall] = []
        self.extra: dict = {}
        self._by_index: dict[int, _ToolCall] = {}

    def feed(self, chunk) -> str:
        if not chunk.choices:
            return ''
        delta = chunk.choices[0].delta
        self.extra.update(_provider_fields(delta))
        for part in delta.tool_calls or ():
            call = self._call_for(part)
            if part.function and part.function.name:
                if _is_json(call.arguments):  # the whole call sent again; other providers repeat the name per fragment
                    call.arguments = ''
                call.name = part.function.name
            if part.function and part.function.arguments:
                call.arguments += part.function.arguments
            call.extra.update(_provider_fields(part))
        self.text += delta.content or ''
        return delta.content or ''

    def _call_for(self, part) -> _ToolCall:
        """The call a streamed fragment belongs to. OpenAI sends the id once and then only the index; other
        providers may send no id, no index, or the same index for parallel calls with different ids."""
        if part.id and (known := next((call for call in self.calls if call.id == part.id), None)):
            return known
        started = self._by_index.get(part.index) if part.index is not None else (self.calls[-1] if self.calls
                                                                                    else None)
        if started and not part.id:
            return started
        call = _ToolCall(part.id or f'call_{len(self.calls)}')
        self.calls.append(call)
        if part.index is not None:
            self._by_index[part.index] = call
        return call

    def assistant_message(self) -> dict:
        return {'role': 'assistant', 'content': self.text or None, **self.extra,
                'tool_calls': [{'id': call.id, 'type': 'function', **call.extra,
                                'function': {'name': call.name, 'arguments': call.arguments or '{}'}}
                               for call in self.calls]}


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _provider_fields(item) -> dict:
    return {key: value for key, value in (item.model_extra or {}).items() if key in PROVIDER_FIELDS}


def _retry_after(error: openai.APIError, attempt: int) -> float:
    """The wait the provider asks for, else 1, 2, 4... s. Gemini writes it in the message ('retry in 39.7s', or
    'retry in 11h38m36.1s' when the daily quota is gone); others use Retry-After."""
    text = f'{error} {getattr(error, "body", "")}'
    if match := re.search(r'retry in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?) ?s', text):
        return int(match[1] or 0) * 3600 + int(match[2] or 0) * 60 + float(match[3])
    response = getattr(error, 'response', None)
    try:
        return float(response.headers['retry-after'])
    except (AttributeError, KeyError, TypeError, ValueError):
        return float(2 ** (attempt - 1))
