"""Baked ASCII banner (#128): color auto-detection, width fitting, and the plain
tagline fallback for narrow terminals / non-color contexts."""

import re
import sys

from tinytalk.branding import banner

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clear_locale(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def test_no_color_env_suppresses_ansi(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b[" not in banner(width=80)


def test_non_tty_suppresses_ansi(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert "\x1b[" not in banner(width=80)


def test_term_dumb_suppresses_ansi(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert "\x1b[" not in banner(width=80)


def test_width_80_fits_80_columns():
    for line in banner(width=80, color=False).splitlines():
        assert len(line) <= 80


def test_narrow_width_returns_plain_tagline(monkeypatch):
    _clear_locale(monkeypatch)
    assert banner(width=20) == "TinyTalk — plain English at the shell"


def test_colored_matches_plain_art_stripped():
    plain = banner(width=80, color=False)
    colored = banner(width=80, color=True)
    assert "\x1b[" in colored
    assert _ANSI.sub("", colored) == plain
