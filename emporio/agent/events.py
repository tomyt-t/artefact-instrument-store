"""Contract between the agent and any interface (web, CLI, tests).

The agent answers a message with an async stream of these events. Interfaces render what they
understand and ignore the rest: the CLI prints the text and describes the actions in one line,
while the storefront reacts to them (cart, catalog, order panel) in real time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TextDelta:
    """A chunk of the assistant's answer (markdown), streamed as it is generated."""
    text: str


@dataclass(frozen=True)
class ToolStatus:
    """The agent started consulting a source; `label` is shown to the customer, e.g. 'Consultando o catálogo…'."""
    tool: str
    label: str


@dataclass(frozen=True)
class Suggestions:
    """Quick replies offered below the answer."""
    options: tuple[str, ...]


@dataclass(frozen=True)
class ShowProducts:
    """Put a selection of products on screen, e.g. the result of 'violões até R$ 1.000'."""
    product_ids: tuple[int, ...]
    title: str


@dataclass(frozen=True)
class OpenProduct:
    product_id: int


@dataclass(frozen=True)
class AddToCart:
    product_id: int
    quantity: int = 1


@dataclass(frozen=True)
class RemoveFromCart:
    product_id: int


@dataclass(frozen=True)
class OpenCart:
    pass


@dataclass(frozen=True)
class ShowOrder:
    """Show an order panel. Agents must only emit it after verifying the customer's identity."""
    order_id: int


UIAction = ShowProducts | OpenProduct | AddToCart | RemoveFromCart | OpenCart | ShowOrder
AgentEvent = TextDelta | ToolStatus | Suggestions | UIAction


def to_dict(event: AgentEvent) -> dict:
    """JSON-friendly form, e.g. for logs or an HTTP/WebSocket API."""
    return {'type': type(event).__name__, **asdict(event)}
