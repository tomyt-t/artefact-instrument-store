"""Tools the agent calls: deterministic Python over the store data, the cart and the policy manual.

The model sees each tool's name, description and JSON Schema (`Toolbox.schemas()`) and calls it with JSON
arguments. `Toolbox.call` validates them and never raises: a bad call comes back as {'erro': ...}, so the model can
fix it or explain the problem to the customer. Storefront actions (highlight products, change the cart, open an
order) come back as events for the agent to stream to the interface.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .. import policies
from ..data import StoreData
from ..i18n import Translator
from .base import AgentContext
from .events import UIAction

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A call the tool can't carry out; the message goes back to the model."""


@dataclass(frozen=True)
class ToolResult:
    data: dict  # what the model reads
    events: tuple[UIAction, ...] = ()  # what the storefront does
    suggestions: tuple[str, ...] = ()  # quick replies offered to the customer after the answer


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments
    run: Callable[..., ToolResult]


class Toolbox:
    """The tools for one turn of one conversation."""

    def __init__(self, store: StoreData, context: AgentContext) -> None:
        self.store = store
        self.context = context
        self.t = Translator(context.language)  # what the customer reads directly
        self.tools = {tool.name: tool for tool in self._tools()}

    def schemas(self) -> list[dict]:
        """The tools in the OpenAI function-calling format."""
        return [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
                                                  'parameters': t.parameters}} for t in self.tools.values()]

    def call(self, name: str, arguments: str | dict | None) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult({'erro': f'Ferramenta desconhecida: {name}.'})
        try:
            args = json.loads(arguments or '{}') if isinstance(arguments, str) else dict(arguments or {})
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        if not isinstance(args, dict):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        args = {key: value for key, value in args.items()
                if key in tool.parameters['properties'] and value not in (None, '')}
        if missing := [key for key in tool.parameters.get('required', ()) if key not in args]:
            return ToolResult({'erro': f'Faltou informar: {", ".join(missing)}.'})
        try:
            return tool.run(**args)
        except ToolError as error:
            return ToolResult({'erro': str(error)})
        except Exception:  # a bug must not end the conversation
            log.exception('tool %s failed with arguments %s', name, sorted(args))  # names only: values may be e-mails
            return ToolResult({'erro': 'Falha interna nesta consulta. Peça desculpas e ofereça o contato da equipe: '
                                       f'{policies.STORE.phone}.'})

    def _tools(self) -> list[Tool]:
        return [
        ]
