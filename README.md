# Empório da Música: customer-service agent

A Python text agent for Empório da Música, a fictional music store in Campo Grande/MS, built for the Artefact AI Engineer challenge. The store's team is overloaded with recurring questions about hours, orders, prices and stock, so the goal is to answer those reliably and leave the rest to the team. It answers from the store's CSVs and policy manual, through a storefront with a chat or a terminal, on `gemini-3.5-flash-lite`, a small model anyone can use with a free key.

An evaluation battery of 86 scripted conversations runs on the real model and checks each answer against the store's data and manual. In the final run, 82 passed, with a median of 3.8 s per turn.

## How to run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Get a free key at <https://aistudio.google.com/apikey>.

```bash
uv sync
cp .env.example .env           # then set EMPORIO_LLM_API_KEY
uv run main.py                 # storefront at http://localhost:8080
uv run python -m emporio.cli   # the same agent in the terminal
uv run pytest                  # 235 tests with a scripted model: no key, no quota
uv run ruff check              # lint
```

Without a key, the storefront still works and the chat explains how to set one up. `EMPORIO_LLM_MODEL` and `EMPORIO_LLM_BASE_URL` in `.env` switch to another model or OpenAI-compatible provider, with no code change.

The battery needs a key and takes over 200 requests, within the free tier's 500 a day:

```bash
uv run python -m evals.run --subset completa --label mine --fresh            # 63 main scenarios
uv run python -m evals.run --subset separados --label mine-holdout --fresh   # 23 separate scenarios
```

## Technical decisions

**Approach: function calling in a custom loop.** The loop (`emporio/agent/llm.py`) uses the `openai` SDK and follows the ReAct pattern with native tool calls, for up to 5 tool rounds per turn. Each of the 9 tools is deterministic Python, and its description tells the model when to use it, which is how the agent chooses between data and policies:

- Catalog: `buscar_produtos`, `ver_produto`.
- Cart: `ver_carrinho`, `adicionar_ao_carrinho`, `remover_do_carrinho`.
- Orders: `consultar_pedido`, which needs the number and e-mail and blocks after 5 failed attempts.
- Policies and store: `consultar_politicas`, `prazos_troca_devolucao`, `info_loja`.

The tools rely on plain functions for the rules with numbers (`emporio/policies.py`: installments, shipping, holidays, return deadlines), and `emporio/agent/views.py` hands the model every value already calculated, so it only has to repeat them.

I wanted the chat to feel responsive, so the storefront reacts while the agent works: products light up, the cart changes and the order panel opens before the answer is finished. A small custom loop keeps that under direct control, which is why I chose it over frameworks such as LangGraph or PydanticAI. They may well support this; I did not compare them. I also ruled out a SQL agent, because the rule that an order needs its number and e-mail would depend on the model instead of code.

**Model: `gemini-3.5-flash-lite` with `medium` reasoning.** I chose a light model on purpose. Anyone can run it with a free key, and since it is not a strong model, a good result has to come from the engineering around it: ready-made values from the tools, rules enforced in code and measured checks. In a control run on the same code, `medium` scored 84 of 86 against 81 of 86 with the provider's default, at the latency cost noted under Limitations. Larger Gemini models were discarded because, on the free tier on November 1, 2026, they allowed only 20 requests a day or gave no answer within 45 s.

**Retrieval: deterministic.** I chose deterministic retrieval over RAG with embeddings: the catalog is a 65-row CSV, where prices and stock must be exact, and the policy manual has 28 numbered sections the model can request by number. Vector search would add infrastructure and a way to return a similar-but-wrong passage, with no clear gain at this size. I also ruled out fine-tuning: facts come from the tools, so they must not be baked into the model, and there is no real conversation data to train on. Neither alternative was tested; I would revisit vector search if the policy base grew or lost its structure.

**Prompt: rules in the prompt, facts in the tools.** The system prompt (`emporio/agent/prompt.py`) holds the persona, Tom, and the manual's service rules, including how to handle out-of-scope questions. Prices, stock and policies come from the tools, already calculated. What has a real effect does not rely on the prompt: an order opens only with its number and e-mail, no tool can apply a discount, and the answer language is decided in code. I also tested a package of alternatives: a 13-rule prompt hydrated per situation through the tool results, stricter tool schemas, a forced first tool call and a check on money values before sending. It did not improve the result, at 81 of 86 against 84 of 86 for the 52 rules in the control run, on single runs with slightly different checks, so I discarded it. These ran together, so the numbers do not separate each one. Only one schema change stayed, because it costs nothing: dates go as DD/MM and the code works out the year.

**Interface: NiceGUI storefront and CLI.** The brief accepts a CLI alone. I added the storefront as care for the product, since a store agent is easier to judge when its actions show. NiceGUI keeps everything in Python and holds the page state on the server, so the agent can update the page in the middle of an answer. All interface text uses translation keys (`emporio/locales/`), for English, French and Portuguese. The agent only emits events (`emporio/agent/events.py`), so the storefront, the CLI and the tests consume them independently.

**History: text only, per visitor.** The storefront keeps 60 messages per visitor across reloads and sends the latest 20 to the model. Tool results stay out, so prices and stock are consulted again rather than repeated from memory.

**Data: both sources.** The brief makes using all the data optional. I chose to use both sources: the CSVs let the agent check stock, prices and orders, and the policy manual lets it apply the store's rules, so the service stays adequate and consistent with both. The CSVs load into typed, read-only objects. In 8 products the description belongs to another instrument, so only name, price, stock and status are trusted.

## Evaluation

`tests/` checks the code with a scripted fake model (`tests/fake_llm.py`), so it needs no key and gives the same result every time. `evals/` measures the agent itself: it plays conversations on the real model with a fixed clock and grades them with deterministic checks, so no model grades the model. Scenarios set values that must appear, phrases that must not, tools that must run and storefront actions that must happen. Every answer is also checked for wrong weekdays and addresses, false promises and money values with no origin.

Final run, on a single pass: 59 of 63 main scenarios and 23 of 23 separate ones, with a median of 3.8 s, a p90 of 6.2 s and a worst turn of 24.3 s. The checks were written for this project, and the separate scenarios are no longer strictly unseen. The four questions suggested in the brief pass.

The 4 failures:

- `parcelas-de-um-valor` (real): the model's reasoning leaked into the answer, in English and quoting prompt rules.
- `en-return` (real, minor): explained the return rule but omitted that it applies to online purchases.
- `data-sem-ano` (strict check): said "4 de novembro", which is correct; the check wanted "04/11".
- `pedido-email-errado` (minor): leaked nothing, but repeated "not found" instead of clearly refusing.

Full conversations, in Portuguese: [main set](evals/results/gemini-3.5-flash-lite.md) and [separate set](evals/results/gemini-3.5-flash-lite-holdout.md).

## Examples

Screenshots from the live site, each from a fresh chat, showing the agent acting on the storefront:

1. [Guitars up to R$1000, a beginner's pick added to the cart](examples/screenshots/1-catalog-and-cart.png): English.
2. [Price of the Takamine GD20, its page, 5 installments](examples/screenshots/2-price-and-product-page.png): English.
3. [Address, opening status, guitars on sale](examples/screenshots/3-store-info-and-offers.png): English.
4. [Return of an order that arrived last week](examples/screenshots/4-return-and-order-en-on-pt-page.png): English on a Portuguese page.
5. [Choosing for classical music, adding to the cart, installments](examples/screenshots/5-classical-guitar-cart-pt.png): Portuguese.

[examples/](examples/) has five more conversations with their tool calls, written by the CLI with no editing.

## Assumptions

- **Language.** The brief and the store's material are in Portuguese, but the application form says "All responses must be in English!". I took that as covering this README, the code comments and the submission. The storefront opens in English and also offers Portuguese and French.
- **"The simplest way possible".** I applied the e-mail's request to the agent: no framework, vector store or database. The storefront goes beyond that on purpose, and the CLI runs the same agent without it.
- **Deadline and delivery.** The e-mail gives the second business day from October 31, 2026. That was a Saturday and November 2 is a holiday, so I read it as November 4 and worked to November 3 to be safe. The e-mail asks for a form and the brief for a reply with the link, so I did both.
- **Dates.** The agent uses the real clock in Campo Grande time. The latest order is from March 22, 2026, so open orders show as long delayed.
- **Contradictions in the manual.** The company table prevails over section 7 for the phone, the address without an accent for the e-mail, section 3.1 over the section 3 table for installments, and the CSV over "mais de 300 instrumentos". Corrections reach the model only with the section they correct (`emporio/manual.py`).
- **Gaps in the manual.** The data has only a delivery forecast, so return deadlines use it, unless the customer gives a date; the team confirms. The manual does not list the metropolitan cities, so the team confirms those too. Holidays are national, state and city ones, plus Good Friday.

## Limitations and what I would do with more time

- **Leaked reasoning.** The model's draft reached the customer in 1 of 230 answers with `medium`. I would add a dedicated check and then a filter before display.
- **Latency.** `medium` is slower: a median of about 3.8 s against 2.1 s, and a p90 of 6.2 s against 3.1 s.
- **Long prompt.** 52 rules go in every turn, which is hard to maintain. I would test the shorter version again, with repeated runs.
- **Single runs.** The model varies: the 52 rules scored 84 in the control run and 82 in the final run, after small changes, so gaps of two or three scenarios are ties. I would repeat the fragile scenarios.
- **Language coverage.** Portuguese is by far the most tested. I would add English and French scenarios.
- **Storefront.** When a question spans several categories, the agent runs one search per category, but the storefront highlights one search at a time, and the answer may not mention every category it consulted. With more time, I would group those results on the storefront.
- **E-mail in logs.** `llm.py` logs tool arguments at INFO level, including e-mails, and the CLI turns that level on. I would mask them.
- **Next steps.** Compare a better model on the same battery, and measure a dedicated installments tool I tried late. For a real client, I would first measure what matters to the store: how many questions get solved without the team, how many need a handoff, and the cost per conversation. Then move to a paid plan, since Google may use free-tier content, add monitoring, and tune the agent on real conversations reviewed with the team.

## How I worked

I worked on this from Saturday, October 31, to Tuesday, November 3, starting in a private working repository. Before implementing, I spent about three hours planning, sketching graphs of the tools, interactions and processes I wanted. I made the architecture and design decisions: how the agent is structured, how each tool should behave, and how to fix it when something went wrong. I tested my hypotheses about the agent on large evaluation runs, compared different approaches, analyzed the errors and kept what worked best, aiming for an agent that performs well on a small, accessible model anyone can run with a free API key. I also got really invested and went beyond the brief, with a storefront that reacts to the agent, three languages and a measured evaluation battery. With that much work moving fast and some decisions changing after closer review, the commits there became disorganized. I rebuilt the history here from the final code as smaller, reviewable commits, re-reading and re-testing each step myself before committing it.

The store's data and policies are in Portuguese, while the recruiting e-mail and form are in English, so the storefront and the agent work in both, to serve any reviewer. French is also available, as a small nod to Artefact's French origins; translations live in key files, so it adds little code. The agent answers in the language of the customer's message, and its rules and tools stay in Portuguese, like the manual they come from. Portuguese is the most tested: of the 86 evaluation scenarios, 77 are in Portuguese, 7 in English and 2 in French.

## Use of coding assistants

I used Claude Code throughout, under my direction. It implemented the code from my decisions, ran the large test batteries that validated my hypotheses, and did repeated adversarial reviews, whose fixes I evaluated. I set the scope, decided the architecture, chose the model and reasoning level, analyzed the errors and decided how to fix them, and read, tested and approved every commit. My workflow was to define what to build or test, have Claude Code implement and measure it, and then review the result carefully, using it to review alongside me on larger changes.
