"""The prompt surface (#102): exact-equality checks on assembly and its conditions."""

from __future__ import annotations

from tinytalk import prompts


def test_t1_system_exact_assembly_with_versions_and_preferences():
    out = prompts.t1_system(
        "OS: TestOS. Shell: zsh. GNU userland: GNU coreutils flags are available.",
        [("ls", "list", None), ("rg", "ripgrep", "14.1.0")],
        ["prefer `rg` over `grep -r` for recursive text search"],
    )
    assert out == (
        prompts.IDENTITY + "\n"
        "OS: TestOS. Shell: zsh. GNU userland: GNU coreutils flags are available.\n\n"
        "Installed tools you should prefer (with their key flags):\n"
        "- ls: list\n"
        "- rg: ripgrep (v14.1.0)"
        "\n\nTool preferences on this system (follow unless the request says otherwise):\n"
        "- prefer `rg` over `grep -r` for recursive text search"
        "\n\nOnly use tools from this list, shell builtins, or tools you are certain "
        "are installed; never invent flags. Commit to exactly one command — never a "
        "list of options or alternatives; if you are unsure, pick your best answer. "
        "Respond with only a JSON object matching this shape, no prose around it:\n"
        + prompts.CONTRACT_SHAPE
    )


def test_t1_system_omits_preference_block_when_none_gate():
    out = prompts.t1_system("OS: X.", [("ls", "list", None)], [])
    assert "Tool preferences" not in out
    assert "- ls: list\n\nOnly use tools" in out


def test_user_message_each_condition():
    assert prompts.user_message("do it") == "do it"
    assert prompts.user_message("do it", cwd=".") == "do it"
    assert prompts.user_message("do it", cwd="") == "do it"
    assert prompts.user_message("do it", cwd="/x") == "do it\n\n(current directory: /x)"
    assert prompts.user_message("do it", session_context="ls") == (
        "do it\n\nRecent commands in this session:\nls"
    )
    assert prompts.user_message("do it", problems=("p1", "p2")) == (
        "do it\n\nA previous attempt was rejected: p1; p2. Fix those issues."
    )


def test_user_message_all_parts_in_order():
    assert prompts.user_message("do it", cwd="/x", session_context="ls", problems=("p1",)) == (
        "do it\n\n(current directory: /x)\n\nRecent commands in this session:\nls"
        "\n\nA previous attempt was rejected: p1. Fix those issues."
    )


def test_static_system_shares_the_contract_shape():
    assert prompts.STATIC_SYSTEM.endswith(prompts.CONTRACT_SHAPE)
    assert "runnable" in prompts.STATIC_SYSTEM  # pinned by test_tiers


def test_enrich_templates_format_cleanly():
    missing = prompts.ENRICH_MISSING_TOOLS.format(tools="a, b")
    assert missing == "These tools are NOT installed on this system — do not use them: a, b"
    doc = prompts.ENRICH_TOOL_DOC.format(tool="rg", help="usage: rg")
    assert doc == "Real documentation for `rg` on this system:\nusage: rg"
