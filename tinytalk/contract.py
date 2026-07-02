"""The structured-output contract every backend must satisfy (PRD §5).

A model completion is turned into a validated `Suggestion`; nothing downstream sees a
raw completion. `danger` here is only the model's *stated* danger — the real
classifier (#4) overrides it later. There is exactly one command: no alternatives,
no options — the model commits to one answer, or the ladder fails (PRD §11).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Danger(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class Suggestion:
    command: str
    explanation: str
    danger: Danger
    confidence: float
    needs: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "explanation": self.explanation,
            "danger": self.danger.value,
            "confidence": self.confidence,
            "needs": list(self.needs),
        }


def contract_json_schema() -> dict:
    """JSON Schema for the contract — basis for native structured output and GBNF."""
    return {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "explanation": {"type": "string"},
            "danger": {"type": "string", "enum": [d.value for d in Danger]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "needs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["command", "explanation", "danger", "confidence", "needs"],
        "additionalProperties": False,
    }
