"""Transport-agnostic provider seam for CLITE.

Mirrors the proven Go #8 transport (`Name()`/`Complete`, `Request`/`Response`/
`Usage`/`Message`/`Tool`/`ToolCall`/`ResponseFormat`) in Python idiom. The seam is
deliberately tiny and transport-agnostic: real backends (Claude Agent SDK, OpenAI
Codex SDK, OpenAI-compatible/local) implement it in sibling issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ResponseFormat(str, Enum):
    TEXT = "text"
    JSON_OBJECT = "json_object"
    TOOL_CALL = "tool_call"
    GRAMMAR = "grammar"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str = ""
    parameters: dict | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Capabilities:
    supports_tool_calling: bool = False
    supports_native_json: bool = False
    supports_grammar: bool = False


@dataclass
class CompletionRequest:
    messages: list[Message]
    tools: list[Tool] = field(default_factory=list)
    response_format: ResponseFormat = ResponseFormat.TEXT
    grammar: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    raw: object | None = None


@runtime_checkable
class Provider(Protocol):
    name: str
    capabilities: Capabilities

    async def complete(self, request: CompletionRequest) -> Completion: ...
