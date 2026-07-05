# E — spec: document the add-on model

**File:** `README.md` (+ any provider table it mirrors)  ·  **Commit:** one  ·  **Depends on:** C, D
(document the shipped behavior, not the intent).

## Problem
After C/D the binary no longer contains Bedrock or the Claude Agent SDK CLI; both arrive as a one-time
download during `tt auth`. A user reading the README's "self-contained binary — no Python, no uv,
nothing to build" claim needs to know (a) that these two backends pull an add-on on first setup, and
(b) how to fetch it by hand for an offline/air-gapped machine.

## Behavior (spec — doc edits)

1. **Install section (README §install):** keep the self-contained-binary framing, but add one sentence
   and remove any absolute "works with zero network" implication: the base binary is small; the
   **AWS Bedrock** and **Claude Agent SDK** backends download a one-time add-on the first time you set
   them up with `tt auth` (needs network at setup).

2. **Provider table + walkthroughs:** mark the `bedrock` and `claude-agent-sdk` rows in the `kind`
   table with a note like "one-time add-on fetched at `tt auth`", and add the same note to the Bedrock
   and Claude `tt auth` walkthrough sections.

3. **New short subsection "Offline / manual add-on install":** for a machine without network at
   `tt auth`, document the manual path — the exact steps `install_addon` automates. Every path, asset
   name, and platform token MUST match `addons.py`/DECISIONS.md byte-for-byte:
   - Version = `tt --version` (e.g. `0.1.0`); the add-on is version-stamped and must match the binary.
   - Download the asset + `.sha256` from the release, tag `v<version>`:
     `https://github.com/pbkimdev/tinytalk/releases/download/v<version>/tt-bedrock-addon.tar.gz`
     (cross-platform) and per-platform `tt-claude-addon-<platform>.tar.gz` where
     `<platform>` ∈ {`macos-arm64`, `linux-x86_64`, `linux-arm64`}.
   - Verify: `shasum -a 256 -c tt-bedrock-addon.tar.gz.sha256`.
   - Unpack to the version-stamped dir `…/tinytalk/addons/<name>/<version>/`, honoring `$XDG_DATA_HOME`
     (defaults to `~/.local/share`): bedrock unpacks the `boto3/`… tree at the directory root; claude
     is a single `claude` binary — `chmod +x` it.

## Acceptance (doc review)
- [ ] README no longer implies Bedrock/Claude work with zero network after install.
- [ ] The manual-install steps are copy-pasteable and match `addons.py` exactly: `addon_dir` layout
      (`$XDG_DATA_HOME/tinytalk/addons/<name>/<version>/`, default `~/.local/share`), the three platform
      tokens, the `tt-bedrock-addon.tar.gz` / `tt-claude-addon-<platform>.tar.gz` asset names, bedrock's
      `boto3/` tree at the root, and claude's single `claude` file (`chmod +x`).
- [ ] Version substitution is explicit (asset URL uses the release tag `v<version>` = `tt --version`).

## Out of scope
- Localized (ko) doc pass — follow the repo's existing i18n flow separately (#74).
