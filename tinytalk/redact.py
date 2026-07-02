"""Secret redaction for session-history context (#35, PRD §8).

Recent shell commands are useful model context but may embed credentials.
Everything here errs toward over-redaction: values are replaced with `***`
before they reach any model. Completeness is a known open risk (PRD §15).
"""

from __future__ import annotations

import re

_MAX_CONTEXT_CHARS = 2000

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value / key: value / --key value forms for secret-ish names
    (
        re.compile(
            r"(?i)([-]{0,2}(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
            r"access[_-]?key|auth|authorization|bearer|credentials?)\b(?:\s*[=:]\s*|\s+))(\S+)"
        ),
        r"\1***",
    ),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "***"),  # OpenAI-style keys
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "***"),  # GitHub tokens
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "***"),  # Slack tokens
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),  # AWS access key id
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\b"), "***"),  # JWT
    (re.compile(r"(://[^/\s:@]+:)[^@\s]+(@)"), r"\1***\2"),  # URL userinfo password
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "***"),  # long hex blobs
)


def redact(text: str) -> str:
    """Redact likely secrets; also caps the context to a sane size (newest kept)."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text[-_MAX_CONTEXT_CHARS:]
