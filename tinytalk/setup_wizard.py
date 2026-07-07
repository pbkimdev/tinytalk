"""`tt setup` — step-by-step interactive TinyTalk setup (#130)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tinytalk.auth import QuestionaryIO, WizardIO, configure_language, run_auth_wizard
from tinytalk.branding import banner
from tinytalk.config import default_config_path
from tinytalk.i18n import _
from tinytalk.rcfile import ensure_block, has_block, zsh_integration_block


_MANUAL_ZSH_LINE = 'eval "$(tt init zsh)"'


# Module-level seams so tests can patch tty detection and the rc target.
def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def _zshrc_path() -> Path:
    # Same rc file the install/uninstall scripts target: ${ZDOTDIR:-$HOME}/.zshrc.
    return Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"


def run_setup_wizard(
    *,
    yes: bool = False,
    from_install: bool = False,
    io: WizardIO | None = None,
    config_path: Path | None = None,
) -> int:
    """Run the interactive setup wizard. Returns a process-style exit code."""
    del from_install  # reserved for installer-specific copy; behavior is identical for now.

    if yes:
        print(banner())
        print()
        print(_("Manual zsh setup: {line}").format(line=_MANUAL_ZSH_LINE))
        print(_("Provider setup: run `tt auth` when you are ready."))
        print(_("Language setup: run `tt setup` in a terminal when you are ready."))
        print(_("You can re-run `tt setup` anytime."))
        return 0

    if not _stdin_isatty():
        print(_("Run 'tt setup' in a terminal to configure TinyTalk interactively."))
        return 0

    config_path = config_path or default_config_path()
    zshrc = _zshrc_path()

    print(banner())
    print()

    io = io or QuestionaryIO()
    marker, block = zsh_integration_block()
    summary: list[tuple[str, str]] = []

    print(_("Step 1 of 3 — zsh integration"))
    if has_block(zshrc, marker):
        print(_("✓ zsh widget already installed in {path}").format(path=zshrc))
        summary.append((_("zsh integration"), str(zshrc)))
    else:
        install = io.confirm(_("Install the tt zsh widget into ~/.zshrc?"), default=True)
        if install:
            ensure_block(zshrc, marker, block)
            print(_("✓ zsh widget installed in {path}").format(path=zshrc))
            summary.append((_("zsh integration"), str(zshrc)))
        else:
            print(_("Manual zsh setup: {line}").format(line=_MANUAL_ZSH_LINE))

    print()
    print(_("Step 2 of 3 — provider"))
    if _has_primary_backend(config_path):
        print(_("✓ primary provider already configured in {path}").format(path=config_path))
        reconfigure = io.confirm(_("Reconfigure the primary provider?"), default=False)
    else:
        reconfigure = True
    if reconfigure:
        # run_auth_wizard reports which slot it wrote — on a re-run the user may
        # configure (or remove) the fallback instead of the primary.
        result = run_auth_wizard(config_path, io)
        if result == "primary":
            print(_("✓ primary provider configured in {path}").format(path=config_path))
            summary.append((_("primary provider"), str(config_path)))
        elif result == "fallback":
            print(_("✓ fallback provider configured in {path}").format(path=config_path))
            summary.append((_("fallback provider"), str(config_path)))
        else:
            print(_("Provider setup skipped."))

    print()
    print(_("Step 3 of 3 — language"))
    language = configure_language(config_path, io)
    if language:
        print(
            _("✓ language set to {language} in {path}").format(language=language, path=config_path)
        )
        summary.append((_("language"), str(config_path)))
    else:
        print(_("Language setup skipped."))

    print()
    print(_("Summary"))
    if summary:
        for label, path in summary:
            print(_("✓ {label}: {path}").format(label=label, path=path))
    else:
        print(_("Nothing was changed."))
    print(_("You can re-run `tt setup` anytime."))
    return 0


def _has_primary_backend(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    try:
        import tomllib

        doc = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    defaults = doc.get("defaults") or {}
    backends = doc.get("backends") or {}
    name = defaults.get("backend")
    return isinstance(name, str) and name in backends
