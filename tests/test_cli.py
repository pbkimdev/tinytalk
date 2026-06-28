import pytest

from clite.cli import build_parser, main


def test_version_exits_zero():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0


def test_no_request_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    assert "clite" in capsys.readouterr().out.lower()


def test_request_is_not_yet_implemented(capsys):
    assert main(["list", "files"]) == 1
    assert "list files" in capsys.readouterr().err
