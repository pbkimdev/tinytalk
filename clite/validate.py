"""Validation & safety ladder (#34, PRD §7) — check before the user ever sees it.

Cheapest first: syntax parse (`zsh -n`), binaries exist, best-effort flag check
against real help text, then rule-based danger classification. Parse/binary/flag
problems reject the suggestion (the tier controller escalates); danger never
rejects — it is surfaced, and the final classification is never *below* the
model's own claim. Over-warning is fine; a destructive false-negative is not.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clite.contract import Danger, Suggestion
from clite.grounding import SystemGrounding
from clite.tiers import ValidationResult

_PARSE_TIMEOUT = 3.0

_BUILTINS = frozenset(
    "cd echo printf export set unset source . alias unalias type command test [ [[ pwd read "
    "exit return true false let shift trap wait jobs fg bg history ulimit umask".split()
)
_KEYWORDS = frozenset(
    "for while until do done if then else elif fi case esac in function select time { } ! coproc".split()
)
# Wrappers whose next word is again a command; sudo additionally drives danger.
_WRAPPERS = frozenset("sudo env nohup nice xargs command builtin exec".split())
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_COMMAND_ENDERS = frozenset({"|", "||", "&&", ";", "&", "(", ")", ";;"})
_REDIRECT = re.compile(r"^\d*(>{1,2}|<{1,3})(&?\d*)?\|?$|^[<>]&$|^&>>?$")
_SAFE_REDIRECT_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})

# Read-only commands (PRD §7). Anything not classified ends up caution (over-warn).
_SAFE_COMMANDS = frozenset(
    "ls du df cat bat less more head tail wc sort uniq cut tr grep egrep fgrep rg awk sed find "
    "fd file stat basename dirname realpath readlink which whereis ps top htop lsof uptime "
    "whoami id uname date cal env printenv tree diff cmp comm column jq yq xxd hexdump strings "
    "md5 md5sum shasum sha256sum ping dig host nslookup netstat man tldr xargs tee open".split()
)
_CAUTION_COMMANDS = frozenset(
    "mv cp chmod chown ln kill pkill killall touch mkdir rmdir rm patch install".split()
)
_MUTATING_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset(
        "add commit push pull merge rebase reset checkout switch restore clean rm mv stash "
        "cherry-pick revert am apply filter-branch gc prune branch tag remote submodule".split()
    ),
    "brew": frozenset("install uninstall remove upgrade update link unlink cleanup tap".split()),
    "pip": frozenset("install uninstall".split()),
    "pip3": frozenset("install uninstall".split()),
    "npm": frozenset("install i uninstall update ci publish link".split()),
    "uv": frozenset("add remove sync pip tool run".split()),
    "docker": frozenset("rm rmi run exec kill stop restart pull push build prune".split()),
    "kubectl": frozenset("apply delete create edit patch scale drain cordon uncordon".split()),
}

_DANGER_ORDER = {Danger.SAFE: 0, Danger.CAUTION: 1, Danger.DESTRUCTIVE: 2}

# Whole-string patterns that are destructive regardless of structure (PRD §7).
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\b[^|;&]*(\s-[a-zA-Z]*[rRf]|\s--recursive|\s--force)"),
    re.compile(r"\brm\s+(-[a-zA-Z]+\s+)*(/|~/?)(\s|$|\*)"),
    re.compile(r"\bdd\b"),
    re.compile(r"\bmkfs(\.|\b)"),
    re.compile(r"\b(shred|wipefs)\b"),
    re.compile(r"\btruncate\b"),
    re.compile(r"\bfind\b.*(-delete|-exec\s+rm)"),
    re.compile(r"\bxargs\b[^|;&]*\brm\b"),
    re.compile(r"\bgit\s+push\b.*(--force\b|--force-with-lease|\s-f\b)"),
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|filter-branch)"),
    re.compile(r"\bchmod\b.*\b777\b"),
    re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}"),  # fork bomb
    re.compile(r"\bkillall\b|\bkill\b\s+(-\S+\s+)*-1\b"),
    re.compile(r">\s*/dev/(r?disk|sd[a-z]|nvme)"),
    re.compile(r"\bcrontab\s+-r\b"),
)

_SUDO_READONLY = frozenset("cat less ls du df find grep lsof dmesg stat head tail".split())


@dataclass(frozen=True)
class _Segment:
    command: str
    args: tuple[str, ...]


class CommandValidator:
    """The tier controller's `Validator` hook. Callable: `Suggestion → ValidationResult`."""

    def __init__(self, grounding: SystemGrounding, *, cwd: str = "."):
        self._grounding = grounding
        self._cwd = cwd
        self._shell = shutil.which("zsh") or shutil.which("sh")

    def __call__(self, suggestion: Suggestion) -> ValidationResult:
        command = suggestion.command

        parse_problem = self._check_parse(command)
        if parse_problem:
            # Unparseable — nothing further is meaningful; over-warn on danger.
            return ValidationResult(
                ok=False, danger=Danger.DESTRUCTIVE.value, problems=(parse_problem,)
            )

        tokens = _tokenize(command)
        segments = _segments(tokens)
        problems = self._check_binaries(segments) + self._check_flags(segments)

        danger = self._classify(command, tokens, segments)
        final = max(danger, suggestion.danger, key=_DANGER_ORDER.__getitem__)
        return ValidationResult(ok=not problems, danger=final.value, problems=tuple(problems))

    # -- ladder step 1: parse ---------------------------------------------------
    def _check_parse(self, command: str) -> str | None:
        if self._shell is None:  # no POSIX shell at all — cannot happen on target OSes
            return None
        try:
            proc = subprocess.run(
                [self._shell, "-n", "-c", command],
                capture_output=True,
                text=True,
                timeout=_PARSE_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"syntax check failed to run: {exc}"
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return f"does not parse: {detail[0] if detail else 'syntax error'}"
        return None

    # -- ladder step 2: binaries exist ------------------------------------------
    def _check_binaries(self, segments: list[_Segment]) -> list[str]:
        problems = []
        for seg in segments:
            name = seg.command
            if name in _BUILTINS or name in _WRAPPERS or name in self._grounding.binaries:
                continue
            if "/" in name:
                if shutil.which(name) is None:
                    problems.append(f"{name}: not found")
                continue
            problems.append(f"{name}: not installed on this system")
        return problems

    # -- ladder step 3: flags exist (best-effort) --------------------------------
    def _check_flags(self, segments: list[_Segment]) -> list[str]:
        problems = []
        for seg in segments:
            long_flags = [a.split("=", 1)[0] for a in seg.args if a.startswith("--") and a != "--"]
            if not long_flags:
                continue
            help_text = self._grounding.help_text(seg.command)
            if not help_text:  # no docs → never false-reject
                continue
            for flag in long_flags:
                if flag not in help_text:
                    problems.append(f"{seg.command}: unknown option {flag}")
        return problems

    # -- ladder step 5: danger classification ------------------------------------
    def _classify(self, command: str, tokens: list[str], segments: list[_Segment]) -> Danger:
        for pattern in _DESTRUCTIVE_PATTERNS:
            if pattern.search(command):
                return Danger.DESTRUCTIVE

        danger = self._redirect_danger(tokens) or Danger.SAFE
        for seg in segments:
            danger = max(danger, _segment_danger(seg), key=_DANGER_ORDER.__getitem__)
            if danger is Danger.DESTRUCTIVE:
                break
        return danger

    def _redirect_danger(self, tokens: list[str]) -> Danger | None:
        for i, tok in enumerate(tokens):
            if tok in (">", ">|", "1>", "&>") and i + 1 < len(tokens):
                target = tokens[i + 1]
                if target in _SAFE_REDIRECT_TARGETS:
                    continue
                path = Path(target.strip("\"'")).expanduser()
                if not path.is_absolute():
                    path = Path(self._cwd) / path
                if path.exists():
                    return Danger.DESTRUCTIVE  # overwrites an existing file (PRD §7)
                return Danger.CAUTION  # creates a file
            if tok in (">>", "2>", "2>>"):
                return Danger.CAUTION
        return None


def _segment_danger(seg: _Segment) -> Danger:
    name, args = seg.command, seg.args
    if name == "sudo":
        payload = args[0] if args else ""
        return Danger.CAUTION if payload in _SUDO_READONLY else Danger.DESTRUCTIVE
    if name in _CAUTION_COMMANDS:
        return Danger.CAUTION
    if name == "sed":
        return Danger.CAUTION if any(a.startswith("-i") for a in args) else Danger.SAFE
    if name in _MUTATING_SUBCOMMANDS:
        sub = next((a for a in args if not a.startswith("-")), "")
        return Danger.CAUTION if sub in _MUTATING_SUBCOMMANDS[name] else Danger.SAFE
    if name == "tar":
        first = args[0] if args else ""
        letters = first.lstrip("-")
        return Danger.CAUTION if ("x" in letters or "c" in letters) else Danger.SAFE
    if name == "curl" or name == "wget":
        writes = any(a in ("-o", "-O", "--output", "--remote-name") for a in args)
        return Danger.CAUTION if writes else Danger.SAFE
    if name in _SAFE_COMMANDS or name in _BUILTINS or name in _KEYWORDS:
        return Danger.SAFE
    return Danger.CAUTION  # unknown commands: over-warn


def _tokenize(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=False, punctuation_chars="|&;()<>")
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return command.split()


def _segments(tokens: list[str]) -> list[_Segment]:
    """Best-effort command-position extraction across pipeline/list operators."""
    segments: list[_Segment] = []
    current: list[str] = []
    expect_command = True
    sudo_payload_pending = False
    skip_next = False
    discard = False  # inside `for x in …` / `case …` headers — no command there

    def flush() -> None:
        nonlocal current
        if current:
            segments.append(_Segment(current[0], tuple(current[1:])))
        current = []

    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in _COMMAND_ENDERS:
            flush()
            expect_command = True
            discard = False
            continue
        if _REDIRECT.match(tok):
            skip_next = not tok.endswith("&")  # `2>&1` carries no separate target token
            continue
        if discard:
            continue
        if expect_command:
            word = tok.strip("`$\"'")
            if word in ("for", "case", "select"):
                discard = True
                continue
            if not word or _ASSIGNMENT.match(word) or word.startswith("-") or word in _KEYWORDS:
                continue  # still looking for the command word
            if word in _WRAPPERS:
                if word == "sudo":
                    sudo_payload_pending = True
                continue  # the next word is the real command
            if sudo_payload_pending:
                segments.append(_Segment("sudo", (word,)))
                sudo_payload_pending = False
            current = [word]
            expect_command = False
        else:
            current.append(tok.strip("\"'"))
    flush()
    return segments
