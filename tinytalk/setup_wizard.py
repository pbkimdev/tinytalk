"""`tt setup` — step-by-step interactive TinyTalk setup (#130)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from tinytalk import i18n
from tinytalk.auth import QuestionaryIO, WizardIO, configure_language, run_auth_wizard
from tinytalk.branding import banner
from tinytalk.config import default_config_path
from tinytalk.i18n import N_, _
from tinytalk.rcfile import ensure_block, has_block, zsh_integration_block


_MANUAL_ZSH_LINE = 'eval "$(tt init zsh)"'


# Module-level seams so tests can patch tty detection and the rc target.
def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def _zshrc_path() -> Path:
    # Same rc file the install/uninstall scripts target: ${ZDOTDIR:-$HOME}/.zshrc.
    return Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"


def _use_select_event_loop() -> None:
    """macOS kqueue rejects the /dev/tty alias device (EINVAL) — exactly the fd
    install.sh hands us via `tt setup --from-install </dev/tty` (#139).
    prompt_toolkit registers stdin with the asyncio loop, so give this process a
    select()-based loop on darwin; select() handles /dev/tty, and the wizard's
    handful of fds makes the selector choice performance-irrelevant."""
    if sys.platform != "darwin":
        return
    import asyncio
    import selectors
    import warnings

    # The policy API is deprecated on 3.14+ but is the only seam prompt_toolkit
    # consults when it creates its loop, and shipped bundles build on 3.12
    # (UV_PYTHON in release.yml). Revisit when prompt_toolkit exposes a
    # loop_factory (policies are slated for removal in 3.16).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class _SelectPolicy(asyncio.DefaultEventLoopPolicy):
            def new_event_loop(self):
                return asyncio.SelectorEventLoop(selectors.SelectSelector())

        asyncio.set_event_loop_policy(_SelectPolicy())


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

    _use_select_event_loop()
    io = io or QuestionaryIO()
    marker, block = zsh_integration_block()
    summary: list[tuple[str, str]] = []

    # Language comes first so the rest of the wizard can render in it (when a
    # catalog exists — see i18n.SUPPORTED). The config value is the explanation
    # language and is written for any answer; the UI override applies only in-run.
    print(_("Step 1 of 3 — language"))
    language = configure_language(config_path, io)
    if language:
        i18n.set_language(re.split(r"[._@-]", language)[0].lower() or None)
        print(
            _("✓ language set to {language} in {path}").format(language=language, path=config_path)
        )
        summary.append((N_("language"), str(config_path)))
    else:
        print(_("Language setup skipped."))

    print()
    print(_("Step 2 of 3 — zsh integration"))
    if has_block(zshrc, marker):
        print(_("✓ zsh widget already installed in {path}").format(path=zshrc))
        summary.append((N_("zsh integration"), str(zshrc)))
    else:
        install = io.confirm(_("Install the tt zsh widget into ~/.zshrc?"), default=True)
        if install:
            ensure_block(zshrc, marker, block)
            print(_("✓ zsh widget installed in {path}").format(path=zshrc))
            summary.append((N_("zsh integration"), str(zshrc)))
        else:
            print(_("Manual zsh setup: {line}").format(line=_MANUAL_ZSH_LINE))

    print()
    print(_("Step 3 of 3 — provider"))
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
            summary.append((N_("primary provider"), str(config_path)))
        elif result == "fallback":
            print(_("✓ fallback provider configured in {path}").format(path=config_path))
            summary.append((N_("fallback provider"), str(config_path)))
        else:
            print(_("Provider setup skipped."))

    print()
    print(_("Summary"))
    if summary:
        for label, path in summary:
            # Labels are N_-marked at append time; translate here so every line
            # honors a language chosen in step 1.
            print(_("✓ {label}: {path}").format(label=_(label), path=path))
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
