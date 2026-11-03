"""Routes the agent's UI actions to the storefront components that registered for them."""
from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..agent.events import UIAction

A = TypeVar('A', bound=UIAction)
# A handler may return a short receipt ('In your cart: …') that the chat shows under the answer.
Handler = Callable[[A], str | None | Awaitable[str | None]]


class UIEventRouter:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def on(self, action_type: type[A], handler: Handler[A]) -> None:
        self._handlers[action_type].append(handler)

    async def dispatch(self, action: UIAction) -> list[str]:
        receipts = []
        for handler in self._handlers[type(action)]:
            result = handler(action)
            if inspect.isawaitable(result):
                result = await result
            if result:
                receipts.append(result)
        return receipts
