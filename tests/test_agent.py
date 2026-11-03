"""The agent loop, offline, on a scripted fake of the OpenAI client (tests/fake_llm.py)."""
import json
from datetime import datetime

import pytest
from fake_llm import call, chunk, fake_agent

from emporio.agent import config, llm
from emporio.agent.base import AgentContext, ChatMessage
from emporio.agent.config import GEMINI_BASE_URL, SETUP, LLMConfig, load_config
from emporio.agent.events import ShowProducts, Suggestions, TextDelta, ToolStatus
from emporio.agent.history import to_messages
from emporio.agent.llm import (SetupAgent)
from emporio.agent.language import answer_language
from emporio.agent.persona import GREETING
from emporio.agent.prompt import system_prompt
from emporio.policies import TIMEZONE

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TIMEZONE)


@pytest.fixture
def run(store):
    async def run(script, message='oi', **context):
        agent, completions = fake_agent(store, script)
        context = AgentContext(session_id='t', now=NOW, **context)
        events = [event async for event in agent.reply(message, context)]
        return events, completions.requests
    return run


async def test_streams_the_answer(run):
    events, requests = await run([[chunk('Oi! '), chunk('Tudo bem?')]], message='oi')
    assert events == [TextDelta('Oi! '), TextDelta('Tudo bem?')]
    [request] = requests
    assert request['stream'] and request['tool_choice'] == 'auto' and len(request['tools']) == 9
    assert 'reasoning_effort' not in request
    assert [m['role'] for m in request['messages']] == ['system', 'user']


async def test_tool_call_streamed_in_pieces_runs(run):
    first = [chunk(tool_calls=[call('c1', 'buscar_produtos', '{"categoria": "Violões", ')]),
             chunk(tool_calls=[{'index': 0, 'function': {'arguments': '"preco_maximo": 1000}'}}])]
    events, requests = await run([first, [chunk('Achei 12 violões até R$ 1.000!')]])
    assert events[0] == ToolStatus('buscar_produtos', 'Consultando o catálogo…')
    assert isinstance(events[1], ShowProducts) and len(events[1].product_ids) == 12
    assert events[2:] == [TextDelta('Achei 12 violões até R$ 1.000!'),
                          Suggestions(('Quais estão em promoção?', 'Qual você recomenda?'))]
    assistant, tool = requests[1]['messages'][-2:]
    assert assistant['tool_calls'] == [{
        'id': 'c1', 'type': 'function',
        'function': {'name': 'buscar_produtos', 'arguments': '{"categoria": "Violões", "preco_maximo": 1000}'}}]
    assert tool['tool_call_id'] == 'c1' and json.loads(tool['content'])['encontrados'] == 12


async def test_tool_errors_go_back_to_the_model(run):
    events, requests = await run([[chunk(tool_calls=[call('a', 'consultar_pedido',
                                                          '{"numero_pedido": 8, "email": "x@y.z"}')])],
                                  [chunk('Não encontrei.')]])
    assert not any(type(e).__name__ == 'ShowOrder' for e in events)
    assert 'erro' in json.loads(requests[1]['messages'][-1]['content'])


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


@pytest.mark.parametrize(('message', 'page', 'expected'), [
    ('How much is the Yamaha C40?', 'pt-BR', 'en'), ('Quanto custa o Yamaha C40?', 'en', 'pt-BR'),
    ('Quelles guitares avez-vous à moins de 1000 R$ ?', 'en', 'fr'), ('Me arrependi da compra, posso devolver?', 'en', 'pt-BR'),
    ('ok', 'fr', 'fr'), ('Yamaha C40', 'en', 'en'), ('But I barely used it... is there really no way?', 'pt-BR', 'en'),
    ('Bonjour, je veux suivre ma commande', 'en', 'fr')])
def test_the_answer_language_follows_the_message_and_then_the_page(message, page, expected):
    assert answer_language(message, page) == expected
