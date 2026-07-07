"""Message catalog for the UI strings TinyTalk itself authors (#74).

A minimal in-repo catalog, not stdlib `gettext`: no `.po`/`.mo` compilation step, no
file I/O at startup, and the `--help`/`--version` cold-start path (cli.py module
docstring) pays only this module's import — `os` and `re`, both already loaded by
`argparse`. English source strings are the catalog keys and the fallback, so with no
supported locale set, output is byte-identical to the untranslated text.

The UI language comes from `LC_ALL` > `LC_MESSAGES` > `LANG` (POSIX override order,
same rule as `config.env_language`, re-implemented here so this module stays off the
config import); any language without a catalog falls back to English. Catalogs live
in `tinytalk/locales/<lang>.py` as a plain `MESSAGES` dict keyed by the English text
— adding a language is one new module plus its entry in `SUPPORTED`. This covers
TinyTalk-authored strings only; model-generated content is #73, and `tt eval`
runtime output stays English by design.
"""

from __future__ import annotations

import os
import re

SUPPORTED = frozenset({"ko"})

_catalogs: dict[str, dict[str, str]] = {}
_override: str | None = None


def set_language(code: str | None) -> None:
    """Runtime override of the UI language (None restores env resolution).

    For flows that learn the user's preference mid-run — `tt setup` asks language
    first and renders the rest of the wizard in it. Deliberately not written to
    `os.environ`, which would leak into child processes (provider probes)."""
    global _override
    _override = code


def language() -> str:
    """UI language code from the locale env; `C`/`POSIX` (or nothing set) mean English."""
    if _override:
        return _override
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(var):
            code = re.split(r"[._@-]", value)[0].lower()
            return "en" if not code or code in ("c", "posix") else code
    return "en"


def _(message: str) -> str:
    """Translate one TinyTalk-authored string; unsupported locale or unknown key → English."""
    lang = language()
    if lang not in SUPPORTED:
        return message
    catalog = _catalogs.get(lang)
    if catalog is None:
        from importlib import import_module

        catalog = import_module(f"tinytalk.locales.{lang}").MESSAGES
        _catalogs[lang] = catalog
    return catalog.get(message, message)


def N_(message: str) -> str:
    """Mark a string constant for catalog extraction without translating it yet (the
    gettext `N_` convention) — the use site translates with `_()` at display time."""
    return message
