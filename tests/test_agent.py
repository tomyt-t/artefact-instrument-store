"""The agent loop, offline, on a scripted fake of the OpenAI client (tests/fake_llm.py)."""
import json
from datetime import datetime

import httpx2
import openai
import pytest
from fake_llm import call, chunk, fake_agent, http_error

from emporio.agent import config, llm
from emporio.agent.base import AgentContext, ChatMessage
from emporio.agent.config import GEMINI_BASE_URL, SETUP, LLMConfig, load_config
from emporio.agent.events import AddToCart, ShowProducts, Suggestions, TextDelta, ToolStatus
from emporio.agent.history import to_messages
from emporio.agent.llm import (BUSY, CART_ALREADY_CHANGED, MAX_CALLS_PER_ROUND, MAX_TOOL_ROUNDS, NO_ANSWER, UNAVAILABLE,
                               SetupAgent)
from emporio.agent.language import answer_language
from emporio.agent.persona import GREETING
from emporio.agent.prompt import system_prompt
from emporio.policies import TIMEZONE

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TIMEZONE)
SIGNATURE = {'google': {'thought_signature': 'c2lnbmF0dXJl'}}


@pytest.fixture
def waits(monkeypatch):
    waited = []

    async def sleep(seconds):
        waited.append(seconds)
    monkeypatch.setattr(llm.asyncio, 'sleep', sleep)
    return waited


@pytest.fixture
def run(store, waits):
    async def run(script, message='oi', **context):
        agent, completions = fake_agent(store, script)
        context = AgentContext(session_id='t', now=NOW, **context)
        events = [event async for event in agent.reply(message, context)]
        return events, completions.requests
    return run


def text_of(events):
    return ''.join(e.text for e in events if isinstance(e, TextDelta))


def tools_run(events):
    return [e.tool for e in events if isinstance(e, ToolStatus)]


async def test_streams_the_answer(run):
    events, requests = await run([[chunk('Oi! '), chunk('Tudo bem?')]], message='oi')
    assert events == [TextDelta('Oi! '), TextDelta('Tudo bem?')]
    [request] = requests
    assert request['stream'] and request['tool_choice'] == 'auto' and len(request['tools']) == 9
    assert 'reasoning_effort' not in request
    assert [m['role'] for m in request['messages']] == ['system', 'user']


async def test_tool_call_streamed_in_pieces_runs_and_its_signature_goes_back(run):
    first = [chunk(tool_calls=[call('c1', 'buscar_produtos', '{"categoria": "Violões", ', extra_content=SIGNATURE)]),
             chunk(tool_calls=[{'index': 0, 'function': {'arguments': '"preco_maximo": 1000}'}}])]
    events, requests = await run([first, [chunk('Achei 12 violões até R$ 1.000!')]])
    assert events[0] == ToolStatus('buscar_produtos', 'Consultando o catálogo…')
    assert isinstance(events[1], ShowProducts) and len(events[1].product_ids) == 12
    assert events[2:] == [TextDelta('Achei 12 violões até R$ 1.000!'),
                          Suggestions(('Quais estão em promoção?', 'Qual você recomenda?'))]
    assistant, tool = requests[1]['messages'][-2:]
    assert assistant['tool_calls'] == [{
        'id': 'c1', 'type': 'function', 'extra_content': SIGNATURE,
        'function': {'name': 'buscar_produtos', 'arguments': '{"categoria": "Violões", "preco_maximo": 1000}'}}]
    assert tool['tool_call_id'] == 'c1' and json.loads(tool['content'])['encontrados'] == 12


async def test_parallel_calls_and_text_in_two_rounds(run):
    first = [chunk('Vou conferir.'),
             chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id": 95}'),
                               call('b', 'info_loja', '{}', index=1)])]
    events, requests = await run([first, [chunk('O GD20 sai por R$ 2.199,00.')]])
    assert tools_run(events) == ['ver_produto', 'info_loja']
    assert text_of(events) == 'Vou conferir.\n\nO GD20 sai por R$ 2.199,00.'
    assert [m['tool_call_id'] for m in requests[1]['messages'][-2:]] == ['a', 'b']


@pytest.mark.parametrize('first', [
    [chunk(tool_calls=[call(None, 'ver_produto', '{"produto_id": 95}'), call(None, 'info_loja', '{}', index=1)])],
    [chunk(tool_calls=[call(None, 'ver_produto', '{"produto_id"', index=0)]),
     chunk(tool_calls=[call(None, 'info_loja', '{}', index=1)]),
     chunk(tool_calls=[{'index': 0, 'function': {'arguments': ': 95}'}}])],
    [chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id": 95}')]),  # same index, another id
     chunk(tool_calls=[call('b', 'info_loja', '{}')])],
    [chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id": 95}'), call('b', 'info_loja', '{}', index=1)]),
     chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id": 95}'), call('b', 'info_loja', '{}', index=1)])],
    [chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id"')]),
     chunk(tool_calls=[call('a', 'ver_produto', ': 95}'), call('b', 'info_loja', '{}', index=1)])],
], ids=['no ids', 'no ids, interleaved', 'one index, two ids', 'calls sent twice', 'name on every fragment'])
async def test_tool_calls_are_assembled_from_any_streaming_style(run, first):
    events, requests = await run([first, [chunk('Pronto.')]])
    assert tools_run(events) == ['ver_produto', 'info_loja']
    calls = requests[1]['messages'][-3]['tool_calls']
    assert [json.loads(c['function']['arguments']) for c in calls] == [{'produto_id': 95}, {}]
    assert len({c['id'] for c in calls}) == 2
    assert not any('erro' in json.loads(m['content']) for m in requests[1]['messages'][-2:])


async def test_tool_errors_go_back_to_the_model(run):
    events, requests = await run([[chunk(tool_calls=[call('a', 'consultar_pedido',
                                                          '{"numero_pedido": 8, "email": "x@y.z"}')])],
                                  [chunk('Não encontrei.')]])
    assert not any(type(e).__name__ == 'ShowOrder' for e in events)
    assert 'erro' in json.loads(requests[1]['messages'][-1]['content'])


async def test_the_last_round_must_answer_without_tools(run):
    looping = [[chunk(tool_calls=[call(f'c{i}', 'info_loja', '{}')])] for i in range(MAX_TOOL_ROUNDS)]
    events, requests = await run([*looping, [chunk('Pronto.')]])
    assert [r['tool_choice'] for r in requests] == ['auto'] * MAX_TOOL_ROUNDS + ['none']
    assert text_of(events) == 'Pronto.'
    # a provider that ignores tool_choice='none': the calls are not run, since the model would never see the result
    ignored = [chunk(tool_calls=[call('z', 'adicionar_ao_carrinho', '{"produto_id": 81}')])]
    events, requests = await run([*looping, ignored])
    assert len(requests) == MAX_TOOL_ROUNDS + 1 and tools_run(events) == ['info_loja'] * MAX_TOOL_ROUNDS
    assert not any(isinstance(e, AddToCart) for e in events) and text_of(events) == NO_ANSWER


async def test_every_turn_ends_with_an_answer(run):
    events, _ = await run([[chunk('')]])
    assert text_of(events) == NO_ANSWER
    preamble = [chunk('Vou conferir o estoque.'), chunk(tool_calls=[call('a', 'ver_produto', '{"produto_id": 95}')])]
    events, _ = await run([preamble, [chunk('')]])
    assert text_of(events) == f'Vou conferir o estoque.\n\n{NO_ANSWER}'


async def test_rate_limit_is_retried_then_explained(run, waits):
    events, requests = await run([http_error(openai.RateLimitError, 429, retry_after='3'), [chunk('Oi!')]])
    assert text_of(events) == 'Oi!' and len(requests) == 2 and waits == [3.0]
    gemini = [{'error': {'code': 429, 'message': 'Quota exceeded. Please retry in 7.5s.'}}]
    events, _ = await run([http_error(openai.RateLimitError, 429, retry_after=None, body=gemini), [chunk('Oi!')]])
    assert text_of(events) == 'Oi!' and waits == [3.0, 7.5]
    long_wait = [{'error': {'message': 'Please retry in 39.7s.'}}]
    events, _ = await run([http_error(openai.RateLimitError, 429, retry_after=None, body=long_wait)])
    assert text_of(events) == BUSY
    events, _ = await run([http_error(openai.RateLimitError, 429)] * 3)
    assert text_of(events) == BUSY
    events, requests = await run([http_error(openai.RateLimitError, 429, retry_after='3600')])
    assert text_of(events) == BUSY and len(requests) == 1  # quota gone: no point waiting
    daily = [{'error': {'message': 'Generate content requests per day limit exceeded. Please retry in 11h38m36.18s.'}}]
    events, requests = await run([http_error(openai.RateLimitError, 429, retry_after=None, body=daily)])
    assert text_of(events) == BUSY and len(requests) == 1  # as Gemini really words it when the day's quota is gone


async def test_server_and_connection_errors(run):
    events, requests = await run([http_error(openai.InternalServerError, 503), [chunk('Oi!')]])
    assert text_of(events) == 'Oi!' and len(requests) == 2
    error = openai.APIConnectionError(request=httpx2.Request('POST', 'https://example.test'))
    events, requests = await run([error] * 3)
    assert text_of(events) == UNAVAILABLE and len(requests) == 3


async def test_odd_output_from_the_provider_becomes_a_friendly_answer(run, caplog):
    events, _ = await run([[chunk('Olha '), ValueError('not JSON')]])
    assert text_of(events) == f'Olha \n\n{UNAVAILABLE}'
    assert 'model call failed' in caplog.text
    caplog.clear()


async def test_a_retry_message_that_makes_no_sense_is_ignored(run, waits):
    garbled = [{'error': {'message': 'Please retry in 1.2.3s.'}}]
    events, _ = await run([http_error(openai.RateLimitError, 429, retry_after=None, body=garbled), [chunk('Oi!')]])
    assert text_of(events) == 'Oi!' and waits == [1.0]


@pytest.mark.parametrize('failure, expected', [
    (http_error(openai.RateLimitError, 429, retry_after='3600'), BUSY),
    (http_error(openai.BadRequestError, 400), UNAVAILABLE),
    ([chunk('')], NO_ANSWER),
], ids=['rate limit', 'error', 'no answer'])
async def test_after_a_cart_change_the_customer_is_told_not_to_repeat_it(run, failure, expected, caplog):
    add = [chunk(tool_calls=[call('a', 'adicionar_ao_carrinho', '{"produto_id": 81}')])]
    events, _ = await run([add, failure], viewing_product_id=81)
    assert any(isinstance(e, AddToCart) for e in events)
    assert text_of(events) == f'{expected} {CART_ALREADY_CHANGED}'
    caplog.clear()


async def test_a_stream_that_never_ends_is_cut(run, monkeypatch, caplog):
    monkeypatch.setattr(llm, 'TURN_SECONDS', -1)
    events, _ = await run([[chunk('Olha'), chunk(' só')]])
    assert text_of(events) == UNAVAILABLE
    caplog.clear()


async def test_too_many_calls_at_once_run_only_up_to_the_limit(run):
    many = [call(f'c{i}', 'info_loja', '{}', index=i) for i in range(MAX_CALLS_PER_ROUND + 2)]
    events, requests = await run([[chunk(tool_calls=many)], [chunk('Pronto.')]])
    assert len(tools_run(events)) == MAX_CALLS_PER_ROUND
    answers = [m for m in requests[1]['messages'] if m['role'] == 'tool']
    assert len(answers) == MAX_CALLS_PER_ROUND + 2 and 'erro' in json.loads(answers[-1]['content'])


async def test_suggestions_come_from_the_last_tool(run):
    calls = [call('a', 'ver_produto', '{"produto_id": 81}'), call('b', 'ver_produto', '{"produto_id": 113}', index=1)]
    events, _ = await run([[chunk(tool_calls=calls)], [chunk('O SN-7C saiu de linha.')]])
    assert not any(isinstance(e, Suggestions) for e in events)  # no 'coloca no carrinho' after a discontinued product


async def test_reasoning_effort_is_sent_only_when_configured(store):
    agent, completions = fake_agent(store, [[chunk('Oi')]], reasoning_effort='low')
    [_ async for _ in agent.reply('oi', AgentContext('t', now=NOW))]
    assert completions.requests[0]['reasoning_effort'] == 'low'


async def test_temperature_is_sent_only_when_configured(store):
    agent, completions = fake_agent(store, [[chunk('Oi')]], temperature=0.0)
    [_ async for _ in agent.reply('oi', AgentContext('t', now=NOW))]
    assert completions.requests[0]['temperature'] == 0.0  # zero is a value, not 'unset'
    agent, completions = fake_agent(store, [[chunk('Oi')]])
    [_ async for _ in agent.reply('oi', AgentContext('t', now=NOW))]
    assert 'temperature' not in completions.requests[0]


def test_history_is_cleaned_before_it_reaches_the_model():
    history = [ChatMessage('assistant', GREETING), ChatMessage('user', 'oi'),
               ChatMessage('assistant', 'O assistente ainda não está implementado.'), ChatMessage('user', '  '),
               ChatMessage('user', 'tem violão?'), ChatMessage('assistant', SETUP), ChatMessage('user', 'e aí?'),
               ChatMessage('assistant', 'Tem sim!')]
    assert to_messages(history, 'x' * 5000) == [
        {'role': 'user', 'content': 'oi\n\ntem violão?\n\ne aí?'}, {'role': 'assistant', 'content': 'Tem sim!'},
        {'role': 'user', 'content': 'x' * 2000}]


def test_broken_unicode_in_the_history_does_not_reach_the_model():
    assert to_messages([], 'oi \ud83d') == [{'role': 'user', 'content': 'oi'}]


def test_prompt_knows_the_date_the_store_and_the_screen(store):
    prompt = system_prompt(NOW, AgentContext('t', cart={'items': [{'product_id': 81, 'quantity': 2}]},
                                             viewing_product_id=95), store)
    assert 'Sábado, 31/10/2026, 12:00' in prompt
    assert 'Loja e equipe: ABERTA agora, até as 13h de hoje; depois disso, abre terça, 03/11/2026, às 9h.' in prompt  # Monday is a holiday
    assert 'Baixos, Baterias e Percussão, Teclados e Pianos, Violões (também' in prompt
    keyboards = [p for p in store.products.values() if store.categories[p.category_id].name == 'Teclados e Pianos']
    assert keyboards and all(p.specs['type'] == 'Synthesizer' for p in keyboards)  # what the prompt says about them
    assert 'Rua 14 de Maio, 3200' in prompt  # asked by the way, the model made up another street
    assert 'Takamine GD20 Dreadnought Natural" (id 95)' in prompt
    assert ('Carrinho do cliente: 2 item(ns), total R$ 1.199,80, no PIX R$ 1.139,80, parcelas: até 11x de R$ 109,07 '
            'sem juros (sem o frete).') in prompt
    assert '(67) 3341-4444' in prompt and '3321-4500' not in prompt and 'consentimento explícito' in prompt
    finados = system_prompt(datetime(2026, 11, 2, 10, tzinfo=TIMEZONE), AgentContext('t'), store)
    assert 'Loja e equipe: FECHADA agora; abre terça, 03/11/2026, às 9h.' in finados
    assert 'Feriados em que a loja fecha nesta semana: segunda, 02/11/2026.' in prompt
    assert 'Feriados' not in system_prompt(datetime(2026, 11, 3, 10, tzinfo=TIMEZONE), AgentContext('t'), store)


def test_a_typo_in_the_temperature_does_not_take_the_site_down(monkeypatch):
    monkeypatch.setenv('EMPORIO_LLM_API_KEY', 'abc')
    monkeypatch.setenv('EMPORIO_LLM_TEMPERATURE', '0,2')
    assert load_config().temperature is None


def test_config_comes_from_the_environment(monkeypatch):
    for name in ('EMPORIO_LLM_API_KEY', 'EMPORIO_LLM_BASE_URL', 'EMPORIO_LLM_MODEL', 'EMPORIO_LLM_REASONING_EFFORT',
                 'EMPORIO_LLM_TEMPERATURE'):
        monkeypatch.delenv(name, raising=False)
    assert load_config() is None
    monkeypatch.setenv('EMPORIO_LLM_API_KEY', ' abc ')
    assert load_config() == LLMConfig('abc', GEMINI_BASE_URL, config.DEFAULT_MODEL, 'medium')
    monkeypatch.setenv('EMPORIO_LLM_BASE_URL', 'http://localhost:11434/v1')
    monkeypatch.setenv('EMPORIO_LLM_MODEL', 'outro')
    assert load_config().base_url == 'http://localhost:11434/v1' and load_config().model == 'outro'


async def test_without_a_key_the_assistant_explains_the_setup(monkeypatch):
    monkeypatch.delenv('EMPORIO_LLM_API_KEY', raising=False)
    agent = llm.create_agent()
    assert isinstance(agent, SetupAgent)
    assert [e async for e in agent.reply('oi', AgentContext('t'))] == [TextDelta(SETUP)]


async def test_the_answer_language_goes_last_and_with_the_tool_data(store):
    script = [[chunk(tool_calls=[call('a', 'info_loja', '{}')])], [chunk('Hi')]]
    agent, completions = fake_agent(store, script)
    [_ async for _ in agent.reply('what time do you close?', AgentContext('t', now=NOW, language='pt-BR'))]
    assert completions.requests[0]['messages'][0]['content'].endswith('Answer in English.')
    assert 'Answer in English.' in completions.requests[1]['messages'][-1]['content']
    agent, completions = fake_agent(store, script)
    [_ async for _ in agent.reply('a que horas vocês fecham?', AgentContext('t', now=NOW, language='en'))]
    assert completions.requests[0]['messages'][0]['content'].endswith('Responda em português do Brasil.')


@pytest.mark.parametrize(('message', 'page', 'expected'), [
    ('How much is the Yamaha C40?', 'pt-BR', 'en'), ('Quanto custa o Yamaha C40?', 'en', 'pt-BR'),
    ('Quelles guitares avez-vous à moins de 1000 R$ ?', 'en', 'fr'), ('Me arrependi da compra, posso devolver?', 'en', 'pt-BR'),
    ('ok', 'fr', 'fr'), ('Yamaha C40', 'en', 'en'), ('But I barely used it... is there really no way?', 'pt-BR', 'en'),
    ('Bonjour, je veux suivre ma commande', 'en', 'fr')])
def test_the_answer_language_follows_the_message_and_then_the_page(message, page, expected):
    assert answer_language(message, page) == expected
