"""The agent behind the chat. No model is connected yet, so it explains how to set one up."""
from __future__ import annotations

from collections.abc import AsyncIterator


from ..i18n import Translator
from ..policies import STORE
from .base import Agent, AgentContext
from .events import AgentEvent, TextDelta


class SetupAgent:
    """Answers every message with how to configure the model, so a fresh clone still runs end to end."""

    async def reply(self, message: str, context: AgentContext) -> AsyncIterator[AgentEvent]:
        yield TextDelta(Translator(context.language)('agent.setup', phone=STORE.phone))


def create_agent() -> Agent:
    return SetupAgent()
