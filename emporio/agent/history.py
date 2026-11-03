"""The conversation so far, cleaned up before it goes to the model."""
from __future__ import annotations

from ..i18n import LANGUAGES, Translator
from ..policies import STORE
from .base import ChatMessage

MAX_MESSAGE_CHARS = 2000  # the storefront stops typing at 600 characters; other interfaces are capped here
# Answers the chat gave while no model was available; they would teach the model to refuse
OBSOLETE_ANSWERS = frozenset({'O assistente ainda não está implementado.',
                              *(Translator(language)(key, phone=STORE.phone) for language in LANGUAGES
                                for key in ('agent.setup', 'agent.busy', 'agent.unavailable', 'chat.error'))})


def to_messages(history: list[ChatMessage], message: str) -> list[dict]:
    """History plus the new message as alternating user/assistant turns, starting with the customer.

    Empty and obsolete answers are dropped, and consecutive turns of the same role are merged (a failed turn
    leaves two customer messages in a row).
    """
    messages: list[dict] = []
    for item in [*history, ChatMessage('user', message)]:
        content = item.content.encode('utf-8', 'ignore').decode().strip()[:MAX_MESSAGE_CHARS]  # no lone surrogates
        if not content or (item.role == 'assistant' and content in OBSOLETE_ANSWERS):
            continue
        if messages and messages[-1]['role'] == item.role:
            messages[-1]['content'] += f'\n\n{content}'
        elif messages or item.role == 'user':
            messages.append({'role': item.role, 'content': content})
    return messages
