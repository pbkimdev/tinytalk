"""i18n for TinyTalk-authored UI strings (#74): locale selection, English fallback,
and Korean catalog completeness (every extracted string translated, no stale entries)."""

import ast
import string
from pathlib import Path

from tinytalk.cli import build_parser
from tinytalk.config import ConfigError, load_config
from tinytalk.i18n import _, language
from tinytalk.locales.ko import MESSAGES as KO

# The modules whose user-facing strings #74 extracts; the completeness test below scans
# exactly these, so a string extracted elsewhere must be added here to be enforced.
_SOURCES = [
    Path(__file__).parent.parent / "tinytalk" / name
    for name in ("cli.py", "auth.py", "config.py")
]


def _clear_locale(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def _extracted_keys() -> set[str]:
    """Every string literal passed to `_()` or `N_()` in the extracted modules."""
    keys = set()
    for source in _SOURCES:
        for node in ast.walk(ast.parse(source.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "N_")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)
    return keys


# --- locale selection ------------------------------------------------------------------


def test_lc_all_beats_lc_messages_and_lang(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "ko_KR.UTF-8")
    assert language() == "ko"
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")  # POSIX override order: LC_ALL wins
    assert language() == "en"


def test_lc_messages_beats_lang(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "ko_KR.UTF-8")
    assert language() == "ko"


def test_no_locale_env_is_english(monkeypatch):
    _clear_locale(monkeypatch)
    assert language() == "en"
    message = "tt: no history yet"
    assert _(message) is message  # byte-identical English, not a copy through the catalog


def test_c_locale_is_english(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LC_ALL", "C")
    assert language() == "en"
    assert _("tt: no history yet") == "tt: no history yet"


def test_unsupported_locale_falls_back_to_english(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert language() == "fr"
    assert _("tt: no history yet") == "tt: no history yet"


def test_korean_locale_translates(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert _("tt: no history yet") == "tt: 아직 히스토리가 없습니다"


def test_unknown_key_falls_back_even_under_korean(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert _("not in any catalog") == "not in any catalog"


# --- catalog completeness ----------------------------------------------------------------


def test_ko_catalog_covers_every_extracted_string_exactly():
    """Every `_()`/`N_()` string has a ko entry (no silent English leak) and the catalog
    carries no stale keys for strings that no longer exist in the source."""
    extracted = _extracted_keys()
    assert extracted, "extraction scan found nothing — the AST scan is broken"
    missing = extracted - set(KO)
    stale = set(KO) - extracted
    assert not missing, f"strings without a ko translation: {sorted(missing)}"
    assert not stale, f"ko catalog keys no longer in the source: {sorted(stale)}"


def test_ko_translations_keep_format_placeholders():
    """Each translation uses exactly the source string's `{...}` fields, so `.format()`
    can never fault on a translated template."""
    formatter = string.Formatter()
    for source, translated in KO.items():
        source_fields = {f for _, f, _, _ in formatter.parse(source) if f}
        translated_fields = {f for _, f, _, _ in formatter.parse(translated) if f}
        assert source_fields == translated_fields, f"placeholder drift in: {source!r}"


# --- end-to-end: help and config errors switch language -----------------------------------


def test_help_is_english_without_locale(monkeypatch):
    _clear_locale(monkeypatch)
    help_text = build_parser().format_help()
    assert "Turn plain English at the shell into a real, validated command." in help_text


def test_help_is_korean_under_ko_locale(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    help_text = build_parser().format_help()
    assert "셸에서 쓴 일상 언어를 실제로 검증된 명령어로 바꿔 줍니다." in help_text
    assert "설정 파일 (기본값: ~/.config/tinytalk)" in help_text


def test_config_error_is_korean_under_ko_locale(tmp_path, monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    try:
        load_config(tmp_path / "missing.toml")
    except ConfigError as exc:
        assert "설정 파일이 없습니다" in str(exc)
    else:
        raise AssertionError("missing config must raise ConfigError")
