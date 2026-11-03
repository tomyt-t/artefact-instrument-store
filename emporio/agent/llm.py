"""The customer-service agent: a language model in a tool-calling loop over the store's tools.

Each turn streams the model's answer. When the model asks for tools, they run (`tools.Toolbox`), their storefront
events go to the interface right away, and their results go back to the model, up to MAX_TOOL_ROUNDS times.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai

from ..data import StoreData, load_store
from ..i18n import STORE_LANGUAGE, Translator
from ..policies import STORE
from .base import Agent, AgentContext
from .config import LLMConfig, load_config
from .events import AgentEvent, Suggestions, TextDelta, ToolStatus
from .history import to_messages
from .language import ANSWER_IN, answer_language
from .prompt import system_prompt
from .tools import OrderGuard, Toolbox

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5  # then the model must answer with what it has
_store_texts = Translator(STORE_LANGUAGE)  # the fixed answers below, in the language the CLI and the evals use
NO_ANSWER = _store_texts('agent.no_answer')


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
        written, suggestions = '', ()

        def fallback(text: str) -> TextDelta:
            """A message of ours, in its own paragraph when the model already wrote something."""
            return TextDelta(('\n\n' if written.strip() else '') + text)

        for round_number in range(MAX_TOOL_ROUNDS + 1):
            step = _Step()
            stream = await self._complete(messages, toolbox, last_round=round_number == MAX_TOOL_ROUNDS)
            async for chunk in stream:
                first_text = not step.text
                if text := step.feed(chunk):
                    if first_text and written and not written.endswith('\n'):
                        yield TextDelta('\n\n')  # a new round continues the answer in a new paragraph
                        written += '\n\n'
                    written += text
                    yield TextDelta(text)
            if not step.calls or round_number == MAX_TOOL_ROUNDS:  # a provider may ignore tool_choice='none'
                break
            messages.append(step.assistant_message())
            for call in step.calls:
                tool = toolbox.tools.get(call.name)
                yield ToolStatus(call.name, t(f'tool.{call.name}' if tool else 'tool.unknown'))
                log.info('tool %s %s', call.name, call.arguments)
                result = toolbox.call(call.name, call.arguments)
                for event in result.events:
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
        return await self.client.chat.completions.create(**request)


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


class _Step:
    """One streamed model response: its text and the tool calls it asks for."""

    def __init__(self) -> None:
        self.text = ''
        self.calls: list[_ToolCall] = []
        self._by_index: dict[int, _ToolCall] = {}

    def feed(self, chunk) -> str:
        if not chunk.choices:
            return ''
        delta = chunk.choices[0].delta
        for part in delta.tool_calls or ():
            call = self._call_for(part)
            if part.function and part.function.name:
                call.name = part.function.name
            if part.function and part.function.arguments:
                call.arguments += part.function.arguments
        self.text += delta.content or ''
        return delta.content or ''

    def _call_for(self, part) -> _ToolCall:
        """The call a streamed fragment belongs to: OpenAI sends the id once and then only the index."""
        started = self._by_index.get(part.index)
        if started and not part.id:
            return started
        call = _ToolCall(part.id or f'call_{len(self.calls)}')
        self.calls.append(call)
        self._by_index[part.index] = call
        return call

    def assistant_message(self) -> dict:
        return {'role': 'assistant', 'content': self.text or None,
                'tool_calls': [{'id': call.id, 'type': 'function',
                                'function': {'name': call.name, 'arguments': call.arguments or '{}'}}
                               for call in self.calls]}
