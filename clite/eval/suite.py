"""The 25-prompt suite + deterministic assertion DSL (#32, PRD §11).

Assertions are `kind:value` strings, checked deterministically against the
generated command — cheaper and more reproducible than an LLM judge (deferred
post-v1):

- `uses:<tool>`        — tool appears in command position (not a substring hit)
- `uses_any:a|b|c`     — any of the tools appears in command position
- `pipes_to:<tool>`    — tool appears in command position after the first stage
- `contains:<text>`    — literal substring
- `not_contains:<text>`— literal substring absent
- `regex:<pattern>`    — `re.search` match
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clite.validate import command_words


@dataclass(frozen=True)
class EvalPrompt:
    id: str
    text: str
    assertions: tuple[str, ...]
    expected_danger: str = "safe"


def check_assertion(assertion: str, command: str) -> bool:
    kind, _, value = assertion.partition(":")
    if kind == "uses":
        return value in command_words(command)
    if kind == "uses_any":
        words = command_words(command)
        return any(tool in words for tool in value.split("|"))
    if kind == "pipes_to":
        return value in command_words(command)[1:]
    if kind == "contains":
        return value in command
    if kind == "not_contains":
        return value not in command
    if kind == "regex":
        return re.search(value, command) is not None
    raise ValueError(f"unknown assertion kind: {kind!r}")


SUITE: tuple[EvalPrompt, ...] = (
    # disk / filesystem
    EvalPrompt(
        "disk-usage-top",
        "Show disk usage of the top-level directories here, human readable, "
        "sorted largest first, top 20",
        ("uses:du", "pipes_to:sort"),
    ),
    EvalPrompt(
        "disk-free",
        "How much free space is left on my disks, human readable",
        ("uses:df", "regex:-[a-zA-Z]*h"),
    ),
    EvalPrompt(
        "list-by-size",
        "List everything in this folder by size, largest first, with the size shown",
        ("uses_any:ls|du|stat",),
    ),
    EvalPrompt(
        "find-large-files",
        "Find files bigger than 100MB under my home directory",
        ("uses_any:find|fd", "contains:100"),
    ),
    EvalPrompt(
        "recent-files",
        "Show the 10 most recently modified files in this directory",
        ("uses_any:ls|find|stat", "contains:10"),
    ),
    # text processing
    EvalPrompt(
        "count-lines-code",
        "Count the total number of lines across all Python files under this directory",
        ("uses_any:wc|awk", "contains:.py"),
    ),
    EvalPrompt(
        "extract-columns",
        "From the file access.log, print only the first and last column of each line",
        ("uses_any:awk|cut", "contains:access.log"),
    ),
    EvalPrompt(
        "replace-in-files",
        "Replace every occurrence of 'foo' with 'bar' in all .txt files here, editing in place",
        ("uses_any:sed|perl", "contains:foo", "contains:bar"),
        expected_danger="caution",
    ),
    EvalPrompt(
        "unique-frequency",
        "Count the unique values in the first column of access.log, most frequent first",
        ("uses_any:sort|awk", "pipes_to:uniq"),
    ),
    EvalPrompt(
        "watch-log",
        "Follow the end of /var/log/system.log as it grows",
        ("uses:tail", "regex:-[0-9]*[fF]", "contains:/var/log/system.log"),
    ),
    # search
    EvalPrompt(
        "grep-todo",
        "Find every TODO in this repo, showing file name and line number",
        ("uses_any:grep|rg", "contains:TODO"),
    ),
    EvalPrompt(
        "find-by-name",
        "Find all files named exactly Makefile anywhere under the current directory",
        ("uses_any:find|fd", "contains:Makefile"),
    ),
    EvalPrompt(
        "grep-recursive-ext",
        "Search for the string 'connect_timeout' in all .yaml files under this directory",
        ("uses_any:grep|rg", "contains:connect_timeout"),
    ),
    # process / system
    EvalPrompt(
        "proc-by-memory",
        "Which processes are using the most memory right now?",
        ("uses_any:ps|top",),
    ),
    EvalPrompt(
        "port-listener",
        "What process is listening on port 8080?",
        ("uses_any:lsof|netstat", "contains:8080"),
    ),
    EvalPrompt(
        "kill-by-name",
        "Stop the running process called ollama",
        ("uses_any:pkill|kill|killall",),
        expected_danger="caution",
    ),
    # networking
    EvalPrompt(
        "public-ip",
        "What is my public IP address?",
        ("uses_any:curl|wget|dig",),
    ),
    EvalPrompt(
        "http-headers",
        "Show only the HTTP response headers for https://example.com",
        ("uses:curl", "regex:(-I|--head)"),
    ),
    EvalPrompt(
        "download-to-tmp",
        "Download https://example.com/data.csv and save it in /tmp",
        ("uses_any:curl|wget", "contains:/tmp"),
        expected_danger="caution",
    ),
    # git
    EvalPrompt(
        "git-recent-commits",
        "Show the last 15 git commits, one line each",
        ("uses:git", "contains:log", "contains:15"),
    ),
    EvalPrompt(
        "git-last-commit-files",
        "Which files changed in the most recent git commit?",
        ("uses:git", "regex:(show|diff|log)"),
    ),
    EvalPrompt(
        "git-delete-branch",
        "Delete my local git branch called old-feature",
        ("uses:git", "contains:branch", "regex:-[dD]", "contains:old-feature"),
        expected_danger="caution",
    ),
    # archive / compress
    EvalPrompt(
        "archive-create",
        "Compress this whole directory into backup.tar.gz, excluding the .git folder",
        ("uses:tar", "contains:backup.tar.gz", "regex:(--exclude|\\.git)"),
        expected_danger="caution",
    ),
    # permissions
    EvalPrompt(
        "make-executable",
        "Make the script deploy.sh executable",
        ("uses:chmod", "contains:deploy.sh"),
        expected_danger="caution",
    ),
    # destructive classification check
    EvalPrompt(
        "delete-node-modules",
        "Completely delete the node_modules folder in this directory",
        ("uses_any:rm|trash", "contains:node_modules"),
        expected_danger="destructive",
    ),
)
