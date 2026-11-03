"""Which language model the agent uses. Everything comes from environment variables or the `.env` file.

Any provider with an OpenAI-compatible chat completions API works; the default is Google's Gemini.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..i18n import STORE_LANGUAGE, Translator
from ..policies import STORE

log = logging.getLogger(__name__)

GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/'
DEFAULT_MODEL = 'gemini-3.5-flash-lite'
GEMINI_REASONING_EFFORT = 'medium'
SETUP = Translator(STORE_LANGUAGE)('agent.setup', phone=STORE.phone)


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = GEMINI_BASE_URL
    model: str = DEFAULT_MODEL
    reasoning_effort: str | None = None  # 'low' | 'medium' | 'high'; None keeps the provider's default
    temperature: float | None = None  # None keeps the provider's default


def _temperature() -> float | None:
    """A typo in this optional setting must not take the storefront down: the provider's default is used instead."""
    value = os.environ.get('EMPORIO_LLM_TEMPERATURE', '').strip()
    try:
        return float(value) if value else None
    except ValueError:
        log.warning('EMPORIO_LLM_TEMPERATURE ignored: %r is not a number', value)
        return None


def _reasoning_effort(base_url: str) -> str | None:
    """Medium on Gemini by default. On other providers nothing is sent unless asked for, because not every model accepts it."""
    chosen = os.environ.get('EMPORIO_LLM_REASONING_EFFORT', '').strip()
    return chosen or (GEMINI_REASONING_EFFORT if base_url.rstrip('/') == GEMINI_BASE_URL.rstrip('/') else None)


def load_config() -> LLMConfig | None:
    """None when no API key is configured: the storefront still works and the chat explains how to set one up."""
    api_key = os.environ.get('EMPORIO_LLM_API_KEY', '').strip()
    if not api_key:
        return None
    base_url = os.environ.get('EMPORIO_LLM_BASE_URL', '').strip() or GEMINI_BASE_URL
    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=os.environ.get('EMPORIO_LLM_MODEL', '').strip() or DEFAULT_MODEL,
        reasoning_effort=_reasoning_effort(base_url),
        temperature=_temperature(),
    )
