from clite.contract import Danger, Suggestion, contract_json_schema
from clite.parsing import parse_payload


def test_danger_values():
    assert [d.value for d in Danger] == ["safe", "caution", "destructive"]


def test_to_dict_round_trips_through_parse_payload():
    original = Suggestion(
        command="ls -la",
        explanation="list files",
        danger=Danger.SAFE,
        confidence=0.9,
        needs=(),
        alternatives=("ls",),
    )
    reparsed = parse_payload(original.to_dict())
    assert reparsed == original


def test_schema_lists_required_keys():
    schema = contract_json_schema()
    assert schema["required"] == ["command", "explanation", "danger", "confidence", "needs"]
    assert "alternatives" in schema["properties"]
    assert "alternatives" not in schema["required"]
