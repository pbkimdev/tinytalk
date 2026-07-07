import json

import pytest

from tinytalk.contract import Danger
from tinytalk.parsing import (
    FormatError,
    extract_json_block,
    parse_completion,
    parse_payload,
    partial_command,
)
from tinytalk.provider.base import Completion, ResponseFormat, ToolCall

VALID = {
    "command": "rm file.txt",
    "explanation": "remove the file",
    "danger": "destructive",
    "confidence": 0.8,
    "needs": [],
}


def test_clean_json_parses():
    s = parse_payload(VALID)
    assert s.command == "rm file.txt"
    assert s.danger is Danger.DESTRUCTIVE
    assert s.needs == ()


def test_fenced_json_block_in_prose():
    text = f"Sure, here you go:\n```json\n{json.dumps(VALID)}\n```\nHope that helps!"
    assert json.loads(extract_json_block(text)) == VALID


def test_generic_fence():
    text = f"```\n{json.dumps(VALID)}\n```"
    assert json.loads(extract_json_block(text)) == VALID


def test_balanced_brace_no_fence_with_trailing_prose():
    text = f"Result: {json.dumps(VALID)} -- that's it."
    assert json.loads(extract_json_block(text)) == VALID


def test_braces_inside_string_value_dont_break_extraction():
    payload = dict(VALID, command="echo '{not json}'")
    text = f"prose {json.dumps(payload)} more prose"
    assert json.loads(extract_json_block(text)) == payload


def test_no_json_raises():
    with pytest.raises(FormatError):
        extract_json_block("there is no json here")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("command"),
        lambda d: d.update(command="   "),
        lambda d: d.update(danger="nuclear"),
        lambda d: d.update(confidence=1.5),
        lambda d: d.update(confidence="high"),
        lambda d: d.update(confidence=True),
        lambda d: d.update(needs="not-a-list"),
        lambda d: d.update(needs=[1, 2]),
        lambda d: d.update(explanation=5),
    ],
)
def test_rejects_malformed(mutate):
    bad = dict(VALID)
    mutate(bad)
    with pytest.raises(FormatError):
        parse_payload(bad)


def test_parse_payload_rejects_non_dict():
    with pytest.raises(FormatError):
        parse_payload([1, 2, 3])


def test_parse_completion_tool_call():
    c = Completion(
        tool_calls=[ToolCall(id="1", name="suggest_command", arguments=json.dumps(VALID))]
    )
    assert parse_completion(c, ResponseFormat.TOOL_CALL).command == "rm file.txt"


def test_parse_completion_tool_call_missing_raises():
    with pytest.raises(FormatError):
        parse_completion(Completion(text="{}"), ResponseFormat.TOOL_CALL)


def test_parse_completion_json_object():
    c = Completion(text=json.dumps(VALID))
    assert parse_completion(c, ResponseFormat.JSON_OBJECT).command == "rm file.txt"


def test_parse_completion_text_extracts():
    c = Completion(text=f"here:\n```json\n{json.dumps(VALID)}\n```")
    assert parse_completion(c, ResponseFormat.TEXT).command == "rm file.txt"


def test_parse_completion_invalid_json_raises():
    with pytest.raises(FormatError):
        parse_completion(Completion(text="{not valid"), ResponseFormat.JSON_OBJECT)


# --- partial_command: best-effort streaming preview (#61) -------------------


def test_partial_command_grows_monotonically_to_the_real_command():
    payload = json.dumps(dict(VALID, command="grep -rn TODO src/"))
    seen = ""
    for i in range(len(payload) + 1):
        got = partial_command(payload[:i])
        assert got.startswith(seen)  # grows monotonically over growing prefixes
        seen = got
    assert partial_command(payload) == "grep -rn TODO src/"


def test_partial_command_decodes_escapes():
    payload = json.dumps({"command": 'echo "hi"\tthere\\done', "explanation": "x"})
    assert partial_command(payload) == 'echo "hi"\tthere\\done'


def test_partial_command_empty_before_key_and_for_non_json():
    assert partial_command("") == ""
    assert partial_command('{"explanation": "listing') == ""  # key not seen yet
    assert partial_command("just some prose with no command key") == ""
    assert partial_command('{"command"') == ""  # key present but value not started


def test_partial_command_stops_at_closing_quote_ignoring_trailing_fields():
    text = '{"command": "ls -la", "danger": "safe"}'
    assert partial_command(text) == "ls -la"


def test_partial_command_drops_dangling_trailing_escape():
    # Mid-stream the buffer can end on a lone backslash (escape started, not finished).
    assert partial_command('{"command": "cd foo\\') == "cd foo"
    assert partial_command('{"command": "cd foo\\n') == "cd foo\n"


def test_partial_command_works_embedded_in_prose():
    text = f"Sure! Here is the plan:\n{json.dumps(VALID)}\nHope that helps."
    assert partial_command(text) == VALID["command"]
