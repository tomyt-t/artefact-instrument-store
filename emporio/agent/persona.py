"""Who the assistant is. Shared by the agent prompt and the chat UI so both introduce it the same way."""
from ..i18n import STORE_LANGUAGE, Translator

ASSISTANT_NAME = 'Tom'

# Policy 7.2: greet, then ask how to help. Tone (7.1): informal but professional, like a friend who knows music.
_t = Translator(STORE_LANGUAGE)
GREETING = _t('chat.greeting', name=ASSISTANT_NAME)
