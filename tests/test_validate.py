"""Validation & safety ladder (#34): parse, binaries, flags, danger classification."""

from __future__ import annotations

import pytest

from clite.contract import Danger, Suggestion
from clite.validate import CommandValidator

# Every binary the test corpus mentions — the validator sees a fixed world.
BINARIES = frozenset(
    "rm dd mkfs.ext4 find git echo chmod killall truncate mv brew sed curl du sort head ls ps "
    "grep df sudo xargs kill tar wget awk sysctl crontab".split()
)


class StubGrounding:
    binaries = BINARIES

    def __init__(self, help_texts: dict[str, str] | None = None):
        self._help = help_texts or {}

    def help_text(self, tool: str) -> str | None:
        return self._help.get(tool)


def validator(cwd: str = ".", help_texts: dict[str, str] | None = None) -> CommandValidator:
    return CommandValidator(StubGrounding(help_texts), cwd=cwd)  # type: ignore[arg-type]


def suggest(command: str, danger: Danger = Danger.SAFE) -> Suggestion:
    return Suggestion(command=command, explanation="", danger=danger, confidence=0.9, needs=())


DESTRUCTIVE = [
    "rm -rf /tmp/build",
    "rm -r node_modules",
    "sudo rm -rf /",
    "dd if=/dev/zero of=/dev/disk2 bs=1m",
    "mkfs.ext4 /dev/sda1",
    "find . -name '*.log' -delete",
    "find /tmp -type f -exec rm {} \\;",
    "find . -name '*.tmp' | xargs rm",
    "git push --force origin main",
    "git push -f origin main",
    "git reset --hard HEAD~3",
    "git clean -fd",
    "truncate -s 0 access.log",
    "killall Dock",
    "kill -9 -1",
    "chmod 777 /etc/passwd",
    "sudo chmod -x /usr/bin/python3",
    "crontab -r",
    ":(){ :|:& };:",
]


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_corpus_is_never_missed(command):
    result = validator()(suggest(command))
    assert result.danger == "destructive", f"false negative: {command}"


def test_redirect_over_existing_file_is_destructive(tmp_path):
    (tmp_path / "data.txt").write_text("precious")
    result = validator(cwd=str(tmp_path))(suggest("echo hi > data.txt"))
    assert result.danger == "destructive"


def test_redirect_to_new_file_is_caution(tmp_path):
    result = validator(cwd=str(tmp_path))(suggest("echo hi > brand-new.txt"))
    assert result.danger == "caution"


def test_redirect_to_dev_null_is_safe():
    result = validator()(suggest("du -h -d1 . 2>/dev/null | sort -hr | head -5"))
    assert result.ok
    assert result.danger == "safe"


CAUTION = [
    "mv report.pdf archive/",
    "brew install jq",
    "chmod +x deploy.sh",
    "sed -i '' 's/old/new/' config.ini",
    "curl -o installer.pkg https://example.com/x.pkg",
    "git checkout main",
    "kill 12345",
    "tar -xzf release.tgz",
]


@pytest.mark.parametrize("command", CAUTION)
def test_mutating_commands_are_at_least_caution(command):
    result = validator()(suggest(command))
    assert result.danger in ("caution", "destructive"), command


SAFE = [
    "du -h -d1 . | sort -hr | head -20",
    "ls -lhS",
    "git log --oneline",
    "ps aux | grep python",
    "find . -name '*.py' -type f",
    "df -h",
    "grep -rn TODO . | head",
    "tar -tzf release.tgz",
    "awk '{print $1, $5}' access.log | sort | head",
]


@pytest.mark.parametrize("command", SAFE)
def test_read_only_commands_are_safe_and_pass(command):
    result = validator()(suggest(command))
    assert result.ok, result.problems
    assert result.danger == "safe", command


def test_model_danger_is_never_downgraded():
    result = validator()(suggest("ls -lhS", danger=Danger.DESTRUCTIVE))
    assert result.danger == "destructive"


def test_unknown_command_is_caution():
    v = CommandValidator(StubGrounding(), cwd=".")  # type: ignore[arg-type]
    v._grounding.binaries = BINARIES | {"frobnicate"}  # type: ignore[misc]
    result = v(suggest("frobnicate --all"))
    assert result.danger == "caution"


def test_syntax_error_is_rejected():
    result = validator()(suggest('echo "unclosed'))
    assert not result.ok
    assert "does not parse" in result.problems[0]


def test_missing_binary_is_rejected():
    result = validator()(suggest("gdu --si"))
    assert not result.ok
    assert any("gdu" in p and "not installed" in p for p in result.problems)


def test_missing_binary_inside_pipeline_is_rejected():
    result = validator()(suggest("ls -la | notreal | head"))
    assert not result.ok
    assert any("notreal" in p for p in result.problems)


def test_unknown_long_flag_is_rejected_when_help_is_known():
    help_texts = {"ls": "usage: ls [-lhS] [--color=when] [--all]"}
    v = validator(help_texts=help_texts)
    bad = v(suggest("ls --frobnicate"))
    assert not bad.ok
    assert "unknown option --frobnicate" in bad.problems[0]
    good = v(suggest("ls --color=auto --all"))
    assert good.ok


def test_flags_skipped_without_help_text():
    result = validator()(suggest("ls --whatever-this-is"))
    assert result.ok  # no docs → never false-reject


def test_wrapped_commands_are_still_checked():
    result = validator()(suggest("sudo notinstalled --go"))
    assert not result.ok
    assert any("notinstalled" in p for p in result.problems)


def test_shell_keywords_and_builtins_are_not_missing_binaries():
    result = validator()(suggest("for f in *.txt; do echo $f; done"))
    assert result.ok, result.problems
