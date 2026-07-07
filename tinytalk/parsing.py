"""Strict parser — the `format_ok` gate (PRD §11–§12).

Any non-conforming payload raises `FormatError`; malformed model output is never
surfaced. This is the single most enforceable eval metric (target 100%).
"""

from __future__ import annotations

import json
import re

from tinytalk.contract import Danger, Suggestion
from tinytalk.provider.base import Completion, ResponseFormat


class FormatError(ValueError):
    """Raised on any payload that does not conform to the contract."""


_COMMAND_KEY = re.compile(r'"command"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def partial_command(text: str) -> str:
    """Best-effort growing value of the top-level `"command"` string from an *incomplete*
    JSON payload — the widget's live preview channel (#61). NEVER raises; returns `""`
    until the value has started.

    `command` is the contract's first field, so the first `"command": "` match is the real
    one (no nested-key disambiguation needed). Decodes standard JSON escapes as it goes and
    stops at the first unescaped `"` (value complete) or end of input (still streaming); a
    dangling trailing backslash (escape started, not finished) is dropped. Works on raw
    tool-call arguments, a bare JSON object, or a JSON object embedded in prose.
    """
    match = _COMMAND_KEY.search(text)
    if match is None:
        return ""
    out: list[str] = []
    i, n = match.end(), len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            break  # unescaped closing quote — value complete
        if ch == "\\":
            if i + 1 >= n:
                break  # dangling escape — value still streaming
            nxt = text[i + 1]
            if nxt == "u":
                hexdigits = text[i + 2 : i + 6]
                if len(hexdigits) < 4:
                    break  # \uXXXX not fully arrived yet
                try:
                    out.append(chr(int(hexdigits, 16)))
                except ValueError:
                    out.append(text[i : i + 6])  # best-effort: keep the raw escape
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_json_block(text: str) -> str:
    """Return the JSON substring from a free-text reply, or raise `FormatError`.

    Prefers a fenced block (```json … ``` then a generic ``` … ```), else scans for
    the first balanced `{ … }` object with a string-aware brace counter.
    """
    fenced = _extract_fenced(text)
    if fenced is not None:
        return fenced
    obj = _first_balanced_object(text)
    if obj is not None:
        return obj
    raise FormatError("no JSON object found in text")


def _extract_fenced(text: str) -> str | None:
    fence = "```"
    start = text.find(fence)
    while start != -1:
        # Skip the fence and an optional language tag on the same line.
        nl = text.find("\n", start + len(fence))
        if nl == -1:
            return None
        body_start = nl + 1
        end = text.find(fence, body_start)
        if end == -1:
            return None
        body = text[body_start:end].strip()
        if body:
            return body
        start = text.find(fence, end + len(fence))
    return None


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def parse_payload(data: object) -> Suggestion:
    """Strictly validate a decoded payload into a `Suggestion` or raise `FormatError`."""
    if not isinstance(data, dict):
        raise FormatError(f"payload is not an object: {type(data).__name__}")

    for key in ("command", "explanation", "danger", "confidence", "needs"):
        if key not in data:
            raise FormatError(f"missing required key: {key}")

    command = data["command"]
    if not isinstance(command, str) or not command.strip():
        raise FormatError("command must be a non-empty string")

    explanation = data["explanation"]
    if not isinstance(explanation, str):
        raise FormatError("explanation must be a string")

    try:
        danger = Danger(data["danger"])
    except ValueError:
        raise FormatError(f"unknown danger: {data['danger']!r}") from None

    confidence = data["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise FormatError("confidence must be a number")
    if not 0.0 <= confidence <= 1.0:
        raise FormatError(f"confidence out of range: {confidence}")

    needs = _string_tuple(data["needs"], "needs")

    return Suggestion(
        command=command,
        explanation=explanation,
        danger=danger,
        confidence=float(confidence),
        needs=needs,
    )


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise FormatError(f"{field_name} must be a list of strings")
    return tuple(value)


def parse_completion(completion: Completion, response_format: ResponseFormat) -> Suggestion:
    """Parse a completion, dispatching on the format the answer was *requested* in."""
    try:
        if response_format is ResponseFormat.TOOL_CALL:
            if not completion.tool_calls:
                raise FormatError("expected a tool call, got none")
            data = json.loads(completion.tool_calls[0].arguments)
        elif response_format in (ResponseFormat.JSON_OBJECT, ResponseFormat.GRAMMAR):
            data = json.loads(completion.text.strip())
        else:  # TEXT
            data = json.loads(extract_json_block(completion.text))
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise FormatError(f"could not decode completion: {exc}") from exc
    return parse_payload(data)
