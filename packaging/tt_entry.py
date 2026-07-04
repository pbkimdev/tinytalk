"""PyInstaller entry point for the standalone `tt` binary.

Kept tiny and import-light on purpose: the CLI's own lazy imports (providers,
SDKs) still defer their cost to first use, exactly as in the pip-installed tool.
The release build (`.github/workflows/release.yml`) freezes this into a
self-contained binary so users install `tt` with no Python or uv on the machine.
"""

import sys

from tinytalk.cli import main

if __name__ == "__main__":
    sys.exit(main())
