"""What an agent receives and how interfaces talk to it."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from .events import AgentEvent


@dataclass(frozen=True)
class ChatMessage:
    role: Literal['user', 'assistant']
    content: str


@dataclass
class AgentContext:
    """Everything the interface knows about the session besides the new message."""
    session_id: str
    history: list[ChatMessage] = field(default_factory=list)
    cart: dict = field(default_factory=dict)
    viewing_product_id: int | None = None
    now: datetime | None = None  # fixed clock for tests and examples; None is the current time in Campo Grande
    verified_orders: set[int] = field(default_factory=set)  # orders whose e-mail the customer confirmed
    language: str = 'pt-BR'  # of the page: the fixed texts follow it; the model answers in the customer's language


class Agent(Protocol):
    def reply(self, message: str, context: AgentContext) -> AsyncIterator[AgentEvent]:
        """Streams the answer to `message` as events (text chunks, tool status and UI actions)."""
        ...
