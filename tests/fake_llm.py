"""A scripted fake of the OpenAI client: replays streamed responses made of real SDK chunks and records requests."""
import copy
from types import SimpleNamespace

import httpx2
from openai.types.chat import ChatCompletionChunk

from emporio.agent.config import LLMConfig
from emporio.agent.llm import LLMAgent


def chunk(content=None, tool_calls=None):
    delta = {'role': 'assistant'}
    if content is not None:
        delta['content'] = content
    if tool_calls is not None:
        delta['tool_calls'] = tool_calls
    return ChatCompletionChunk.model_validate({
        'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fake',
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})


def call(id_, name, arguments, index=0, **extra):
    part = {'index': index, 'type': 'function', 'function': {'name': name, 'arguments': arguments}, **extra}
    return part | {'id': id_} if id_ else part


def http_error(cls, status, retry_after='0', body=None):
    response = httpx2.Response(status, headers={'retry-after': retry_after} if retry_after else {},
                               request=httpx2.Request('POST', 'https://example.test'))
    return cls('erro', response=response, body=body)


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step

        async def stream():
            for item in step:
                if isinstance(item, Exception):
                    raise item
                yield item
        return stream()


def fake_agent(store, script, **config):
    completions = FakeCompletions(script)
    agent = LLMAgent(LLMConfig('key', **config), store=store,
                     client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    return agent, completions
