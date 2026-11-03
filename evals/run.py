"""Runs the scenarios on the real model and saves the result: `uv run python -m evals.run --subset nucleo`.

Each run goes to evals/results/<model>.json (kept between runs, so an interrupted battery resumes where it stopped)
and evals/results/<model>.md (the summary, the failures and every conversation).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from emporio.agent.base import AgentContext, ChatMessage
from emporio.agent.config import load_config
from emporio.agent.events import TextDelta, ToolStatus
from emporio.agent.llm import LLMAgent
from emporio.agent.persona import ASSISTANT_NAME
from emporio.cart import Cart
from emporio.data import load_store
from emporio.i18n import Translator
from emporio.policies import STORE

from .checks import Scenario, check_turn, describe
from .holdout import HOLDOUT
from .scenarios import SCENARIOS

RESULTS = Path(__file__).parent / 'results'
QUOTA_RETRIES = 4
QUOTA_WAIT = 65  # seconds; free tiers limit requests per minute


class QuotaError(Exception):
    """The model answered with the fallback for rate limits or outages: the turn says nothing about the agent."""


def record_requests(agent: LLMAgent) -> list[dict]:
    """The messages of the agent's latest model call, whose tool results are what the answer can rely on."""
    latest: list[dict] = []
    create = agent.client.chat.completions.create

    async def recording(**request):
        latest[:] = request['messages']
        return await create(**request)
    agent.client.chat.completions.create = recording
    return latest


async def run_scenario(agent: LLMAgent, scenario: Scenario, pause: float, requests: list[dict]) -> dict:
    cart = Cart(agent.store, {})
    for product_id, quantity in scenario.cart:
        cart.add(product_id, quantity)
    t = Translator(scenario.language)
    refusals = [t(key, phone=STORE.phone) for key in ('agent.busy', 'agent.unavailable')]
    history = [ChatMessage('assistant', t('chat.greeting', name=ASSISTANT_NAME))]
    verified: set[int] = set()
    turns = []
    sources = ''
    for turn in scenario.turns:
        context = AgentContext(session_id=scenario.id, history=list(history), cart=cart.snapshot(), now=scenario.now,
                               viewing_product_id=scenario.viewing, verified_orders=verified, language=scenario.language)
        started = time.perf_counter()
        answer, tools, events = '', [], []
        async for event in agent.reply(turn.message, context):
            if isinstance(event, TextDelta):
                answer += event.text
            elif isinstance(event, ToolStatus):
                tools.append(event.tool)
            elif action := describe(event):
                events.append(action)
                if action.startswith('AddToCart'):
                    cart.add(event.product_id, event.quantity)
                elif action.startswith('RemoveFromCart'):
                    cart.remove(event.product_id)
        if any(refusal in answer for refusal in refusals):
            raise QuotaError(answer)
        results = [message['content'] for message in requests if message['role'] in ('system', 'tool')]
        sources = ' '.join([sources, turn.message, *results])
        turns.append({'message': turn.message, 'answer': answer, 'tools': tools, 'events': events,
                      'seconds': round(time.perf_counter() - started, 1),
                      'failures': check_turn(turn, answer, tools, events, sources, scenario.now)})
        history += [ChatMessage('user', turn.message), ChatMessage('assistant', answer)]
        await asyncio.sleep(pause)
    return {'id': scenario.id, 'category': scenario.category, 'core': scenario.core, 'turns': turns,
            'passed': not any(turn['failures'] for turn in turns)}


def rescore(results: dict[str, dict]) -> None:
    """Applies the current checks to the saved answers, without calling the model. Checking values for an origin needs
    the tool results, which are not saved, so those failures stay as they were."""
    by_id = {scenario.id: scenario for scenario in SCENARIOS + HOLDOUT}
    for key, run in results.items():
        scenario = by_id[key.rsplit('#', 1)[0]]
        for turn, saved in zip(scenario.turns, run['turns'], strict=True):
            kept = [failure for failure in saved['failures'] if failure.startswith('valor sem origem')]
            saved['failures'] = kept + check_turn(turn, saved['answer'], saved['tools'], saved['events'], now=scenario.now)
        run['passed'] = not any(saved['failures'] for saved in run['turns'])


def write_report(path: Path, model: str, results: dict[str, dict]) -> None:
    runs = list(results.values())
    passed = sum(run['passed'] for run in runs)
    seconds = sorted(turn['seconds'] for run in runs for turn in run['turns'])
    lines = [f'# Avaliação: {model}', '', f'**{passed} de {len(runs)} execuções passaram.** '
             f'Turnos: {len(seconds)}; tempo por turno: mediana {seconds[len(seconds) // 2]} s, máximo {seconds[-1]} s.',
             '', '| Categoria | Passaram |', '| --- | --- |']
    categories = sorted({run['category'] for run in runs})
    for category in categories:
        group = [run for run in runs if run['category'] == category]
        lines.append(f'| {category} | {sum(run["passed"] for run in group)} de {len(group)} |')
    lines += ['', '## Falhas', '']
    failed = [(key, run) for key, run in results.items() if not run['passed']]
    for key, run in failed:
        for number, turn in enumerate(run['turns'], 1):
            lines += [f'- `{key}`, turno {number}: {failure}' for failure in turn['failures']]
    if not failed:
        lines.append('Nenhuma.')
    lines += ['', '## Conversas', '']
    for key, run in results.items():
        lines += [f'### {key} ({"passou" if run["passed"] else "falhou"})', '']
        for turn in run['turns']:
            lines += [f'**Cliente:** {turn["message"]}', '', f'**Tom:** {turn["answer"]}', '',
                      f'> ferramentas: {", ".join(turn["tools"]) or "nenhuma"} · vitrine: '
                      f'{", ".join(turn["events"]) or "nada"} · {turn["seconds"]} s', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


async def main(args: argparse.Namespace) -> int:
    if args.rescore:
        data_path = RESULTS / f'{args.rescore}.json'
        results = json.loads(data_path.read_text(encoding='utf-8'))
        rescore(results)
        data_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        write_report(data_path.with_suffix('.md'), args.rescore, results)
        print(f'{sum(run["passed"] for run in results.values())} of {len(results)} passed.')
        return 0
    config = load_config()
    if config is None:
        print('Set EMPORIO_LLM_API_KEY in .env before running the evaluation.', file=sys.stderr)
        return 2
    if args.model:
        config = replace(config, model=args.model)
    if args.reasoning:
        config = replace(config, reasoning_effort=args.reasoning)
    if args.temperature is not None:
        config = replace(config, temperature=args.temperature)
    pool = HOLDOUT if args.subset == 'separados' else [s for s in SCENARIOS if s.core or args.subset == 'completa']
    scenarios = [s for s in pool if not args.only or s.id in args.only]
    RESULTS.mkdir(exist_ok=True)
    data_path = RESULTS / f'{config.model}{f"-{args.label}" if args.label else ""}.json'
    results: dict[str, dict] = {}
    if args.fresh:  # an outage before the first result must not leave old answers to be resumed as new ones
        data_path.unlink(missing_ok=True)
    elif data_path.exists():
        results = json.loads(data_path.read_text(encoding='utf-8'))
    agent = LLMAgent(config, store=load_store())
    requests = record_requests(agent)
    pending = [(f'{s.id}#{n}', s) for s in scenarios for n in range(1, args.repeat + 1)]
    for number, (key, scenario) in enumerate(pending, 1):
        if key in results and not args.only:
            continue
        for _attempt in range(QUOTA_RETRIES):
            try:
                results[key] = await run_scenario(agent, scenario, args.pause, requests)
                break
            except QuotaError:
                print(f'    provider unavailable or quota used up at {key}, waiting {QUOTA_WAIT} s', flush=True)
                await asyncio.sleep(QUOTA_WAIT)
        else:
            print(f'Stopped at {key}. Run again to continue from there.', file=sys.stderr)
            break
        print(f'[{number}/{len(pending)}] {key}: {"ok" if results[key]["passed"] else "FAILED"}', flush=True)
        for turn in results[key]['turns']:
            for failure in turn['failures']:
                print(f'    {failure}', flush=True)
        data_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    if results:
        write_report(data_path.with_suffix('.md'), config.model, results)
        print(f'{sum(run["passed"] for run in results.values())} of {len(results)} passed. '
              f'Resultado em {data_path.with_suffix(".md")}')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog='python -m evals.run', description='Evaluates the agent on the real model.')
    parser.add_argument('--subset', choices=['nucleo', 'completa', 'separados'], default='nucleo')
    parser.add_argument('--repeat', type=int, default=1, help='runs per scenario')
    parser.add_argument('--model', help='overrides EMPORIO_LLM_MODEL')
    parser.add_argument('--reasoning', help='overrides EMPORIO_LLM_REASONING_EFFORT')
    parser.add_argument('--temperature', type=float, help='overrides EMPORIO_LLM_TEMPERATURE')
    parser.add_argument('--label', help='saves apart, to compare settings without touching the main result')
    parser.add_argument('--only', nargs='*', help='scenario ids (runs again even if there is a result)')
    parser.add_argument('--pause', type=float, default=2.0, help='seconds between turns, to respect the quota')
    parser.add_argument('--fresh', action='store_true', help='ignores previous results')
    parser.add_argument('--rescore', metavar='MODEL', help='reapplies the checks to the saved answers, without calling the model')
    sys.exit(asyncio.run(main(parser.parse_args())))
