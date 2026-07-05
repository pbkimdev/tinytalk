"""Pin `tinytalk.__version__` to pyproject's version so the two never drift.

The version is a literal (not read from package metadata) to keep `import tinytalk` cheap and
to stay robust inside the PyInstaller binary; this test is what guarantees it stays correct —
it also feeds the add-on dir stamp and release-asset URLs."""

from __future__ import annotations

import tomllib
from pathlib import Path

from tinytalk import __version__


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert __version__ == data["project"]["version"]
