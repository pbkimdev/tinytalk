"""Every model-facing string in TinyTalk, in one place — the prompt surface (#102).

Prompt flow (tiers.py drives it; grounding.py supplies host data):

  T0  cache hit — no prompt is sent at all.
  T1  system prompt = t1_system(...), assembled in order:
        1. IDENTITY — who the model is, the one-command rule
        2. host facts — HOST_FACTS_MACOS on Darwin, else HOST_FACTS_GNU
        3. "Installed tools you should prefer (with their key flags):" — one
           "- name: desc" line per CURATED_TOOLS entry whose binary is on
           $PATH, with " (vX.Y)" appended when the snapshot knows the version
        4. tool-preference block — one "- rule" line per PREFERENCE_RULES
           entry whose gate tool is installed; whole block omitted when none
        5. trailer — only-these-tools / never-invent-flags / commit-to-one,
           then EXPLANATION_LANGUAGE when the configured language is not
           English, ending in CONTRACT_SHAPE
  T2  system prompt = T1 system + "\\n\\n" + enrichment (grounding.enrich),
      where enrichment is "\\n\\n"-joined sections:
        - ENRICH_MISSING_TOOLS       when the failed attempt named tools not
                                     on this host
        - ENRICH_TOOL_DOC (per tool) when real --help/man text was fetched
  user message (every model call) = user_message(...), "\\n\\n"-joined parts:
        1. the request itself                        always
        2. "(current directory: ...)"                when cwd set and != "."
        3. "Recent commands in this session:\\n..."   when session context set
        4. "A previous attempt was rejected: ..."    T2 only, when T1's
                                                     suggestion was rejected

STATIC_SYSTEM replaces the T1 system prompt when no real grounding is wired
(tests, pre-#33 fallback). CONTRACT_TOOL_DESCRIPTION is what the model reads
on the suggest_command tool during native tool-calling; the JSON schema stays
in contract.py (structure, not prose), and the tool *name* is a protocol
identifier owned by the providers. Eval suite texts (eval/suite.py) are user
requests under test, not prompt surface — they stay put.

This module imports nothing from tinytalk. CONTRACT_SHAPE contains literal
braces — concatenate it, never pass it through .format().
"""

from __future__ import annotations

from collections.abc import Sequence

IDENTITY = (
    "You are TinyTalk. Turn the user's plain-English request into exactly one "
    "runnable shell command (a pipeline counts as one command) for their system."
)

HOST_FACTS_MACOS = (
    "OS: {name}. Shell: {shell}. macOS (BSD userland): coreutils are BSD variants — "
    "GNU-only long options often do not exist (e.g. use `sed -i ''`, `du -d 1`, `stat -f`)."
)
HOST_FACTS_GNU = "OS: {name}. Shell: {shell}. GNU userland: GNU coreutils flags are available."

# Seeded from tldr one-liners: purpose + the flags that matter for composing
# pipelines. Only entries whose binary is installed are injected.
CURATED_TOOLS: dict[str, str] = {
    "ls": "list directory contents; -l long, -h human sizes, -S sort by size, -t by mtime, -a all",
    "du": "disk usage; -h human, -d N depth (BSD & GNU), -s summary",
    "df": "filesystem free space; -h human",
    "find": "walk a file tree; -name, -type f/d, -size, -mtime, -maxdepth, -exec … \\;",
    "fd": "fast find alternative; pattern first, -e ext, -t f/d, -H hidden",
    "grep": "search text; -r recursive, -i ignore case, -n line numbers, -l files only, -E extended regex",
    "rg": "ripgrep, fast recursive search; -i, -l, -n, -t type, --hidden",
    "awk": "field/record processing; '{print $1}' prints first column, -F sets delimiter",
    "sed": "stream editor; s/old/new/g substitution, -n suppress, -i in-place (BSD needs -i '')",
    "sort": "sort lines; -n numeric, -h human sizes, -r reverse, -k field, -u unique",
    "uniq": "collapse repeated lines (input must be sorted); -c count",
    "head": "first lines; -n N",
    "tail": "last lines; -n N, -f follow",
    "wc": "count; -l lines, -w words, -c bytes",
    "cut": "extract columns; -d delimiter, -f fields",
    "tr": "translate/delete characters",
    "xargs": "build command lines from stdin; -I {} placeholder, -0 null-delimited",
    "cat": "concatenate files to stdout",
    "stat": "file metadata (BSD: stat -f fmt; GNU: stat -c fmt)",
    "cp": "copy files/directories; -R recursive, -p preserve mode/time, -n no clobber, -v verbose",
    "mv": "move or rename files; -n no clobber, -v verbose",
    "rm": "remove files/directories; -r recursive, -f force, -i prompt before remove",
    "touch": "create files or update mtimes; -t timestamp, -a access time, -m modify time",
    "pwd": "print current working directory; -P physical path, -L logical path",
    "dirname": "strip final path component",
    "basename": "strip directory and suffix from a path",
    "readlink": "resolve symlinks; -f canonical path on GNU, macOS lacks -f by default",
    "realpath": "print canonical absolute path; -m allow missing components on GNU",
    "env": "run or print environment; VAR=value command, -i empty env, -u unset variable",
    "which": "locate a command on PATH",
    "tee": "copy stdin to files and stdout; -a append",
    "tar": "archives; -c create, -x extract, -z gzip, -f file, -t list",
    "gzip": "compress; -d decompress, -k keep",
    "zip": "zip archives; -r recursive",
    "unzip": "extract zip; -l list",
    "curl": "HTTP client; -s silent, -L follow redirects, -o file, -X method, -H header",
    "wget": "download files; -O output",
    "git": "version control; log/diff/status/branch; --oneline, -p patches",
    "ps": "processes; aux for all (BSD-style on macOS)",
    "lsof": "open files/ports; -i :PORT who listens",
    "kill": "signal a process; -9 SIGKILL (last resort)",
    "chmod": "change permissions; -R recursive, numeric or u+x modes",
    "chown": "change owner; -R recursive",
    "ln": "links; -s symbolic",
    "mkdir": "make directories; -p parents",
    "rsync": "sync files; -a archive, -v verbose, -n dry-run, --delete",
    "jq": "JSON processor; '.field', '.[]', -r raw output",
    "brew": "Homebrew package manager; install/uninstall/list/info",
    "docker": "containers/images; ps, images, logs, exec -it, run --rm, build, compose up/down/logs",
    "kubectl": "Kubernetes CLI; get/describe/logs/apply/delete, -n namespace, -A all namespaces, -o yaml/json/wide",
    "helm": "Kubernetes package manager; list/install/upgrade/uninstall, -n namespace, -f values.yaml, --set key=value",
    "bash": "Bash shell; -c command, -lc login command, set -euo pipefail, printf/read/for/while/if builtins",
    "sh": "POSIX shell; -c command, portable scripts and pipelines",
    "printf": "format text portably; '%s\\n' for safe line output, avoid echo for escaped data",
    "uname": "system information; -a all, -s kernel name, -m machine",
    "hostname": "show or set hostname; -f fully qualified name where supported",
    "id": "user/group identity; -u user id, -g group id, -n names",
    "whoami": "print effective username",
    "openssl": "crypto toolkit; digests, certs",
    "diff": "compare files; -u unified, -r recursive",
    "date": "print/format date (BSD: date -v+1d; GNU: date -d tomorrow)",
}

# (gate_tool, rule) — a rule is injected only when gate_tool is installed, so the
# prompt never advertises a tool the host lacks.
PREFERENCE_RULES: tuple[tuple[str, str], ...] = (
    ("rg", "prefer `rg` over `grep -r` for recursive text search"),
    ("fd", "prefer `fd` over `find` for finding files by name"),
    ("jq", "prefer `jq` over grep/sed/awk for extracting fields from JSON"),
    ("rsync", "prefer `rsync -a` over `cp -R` for syncing or mirroring directories"),
    ("printf", "prefer `printf` over `echo -e` for output with escapes or variables"),
)

CONTRACT_SHAPE = (
    '{"command": "...", "explanation": "...", '
    '"danger": "safe|caution|destructive", "confidence": 0.0-1.0, '
    '"needs": ["binaries", "used"]}'
)

# English is the identity: no clause is added, so default prompts stay byte-identical.
LANGUAGE_NAMES = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
}
EXPLANATION_LANGUAGE = (
    'Write the "explanation" value in {language}. Everything else — the command '
    "itself, JSON keys, and danger values — stays exactly as specified."
)

STATIC_SYSTEM = (
    IDENTITY + " Commit to exactly one command — never a list of options or alternatives. "
    "Respond with only a JSON object matching this shape, no prose around it:\n" + CONTRACT_SHAPE
)

CONTRACT_TOOL_DESCRIPTION = "Return the validated command suggestion."

ENRICH_MISSING_TOOLS = "These tools are NOT installed on this system — do not use them: {tools}"
ENRICH_TOOL_DOC = "Real documentation for `{tool}` on this system:\n{help}"


def t1_system(
    host_facts: str,
    tools: Sequence[tuple[str, str, str | None]],
    preferences: Sequence[str],
    language: str = "en",
) -> str:
    """The grounded T1 system prompt. `tools` is (name, description, version-or-None)
    already filtered to installed; `preferences` already gated on the preferred tool."""
    lines = [
        f"- {name}: {desc} (v{version})" if version else f"- {name}: {desc}"
        for name, desc, version in tools
    ]
    preference_block = (
        "\n\nTool preferences on this system (follow unless the request says otherwise):\n"
        + "\n".join(f"- {rule}" for rule in preferences)
        if preferences
        else ""
    )
    normalized = language.strip().lower()
    language_clause = (
        ""
        if normalized in ("", "en", "english")
        else EXPLANATION_LANGUAGE.format(language=LANGUAGE_NAMES.get(normalized, language)) + " "
    )
    return (
        f"{IDENTITY}\n"
        f"{host_facts}\n\n"
        "Installed tools you should prefer (with their key flags):\n"
        + "\n".join(lines)
        + preference_block
        + "\n\nOnly use tools from this list, shell builtins, or tools you are certain "
        "are installed; never invent flags. Commit to exactly one command — never a "
        "list of options or alternatives; if you are unsure, pick your best answer. "
        + language_clause
        + "Respond with only a JSON object matching this shape, no prose around it:\n"
        + CONTRACT_SHAPE
    )


def user_message(
    prompt: str, *, cwd: str = ".", session_context: str = "", problems: tuple[str, ...] = ()
) -> str:
    """The user message for every model call — each part gated by its condition."""
    parts = [prompt]
    if cwd and cwd != ".":
        parts.append(f"(current directory: {cwd})")
    if session_context:
        parts.append(f"Recent commands in this session:\n{session_context}")
    if problems:
        parts.append(
            "A previous attempt was rejected: " + "; ".join(problems) + ". Fix those issues."
        )
    return "\n\n".join(parts)
