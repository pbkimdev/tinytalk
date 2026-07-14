# Reuse Claude Code Bedrock authentication in TinyTalk

Status: implementation source of truth; rc11 installer recovery complete

Date: 2026-07-14

## Context

TinyTalk already authenticates its Bedrock provider through boto3's standard AWS credential chain.
Its auth wizard can save a region, an optional named profile, an optional runtime endpoint, and a
model into `config.toml`. It never needs to own the underlying AWS secret.

Claude Code can persist the same non-secret connection settings in the user's
`~/.claude/settings.json`. A user who has already configured and verified Claude Code against
Bedrock currently has to repeat or manually copy that setup into TinyTalk.

Add an explicit import path to the Bedrock branch of `tt auth`. When a complete user-level Claude
Code Bedrock setup is detected, TinyTalk shows the non-secret values it found and offers to reuse
them. After confirmation, TinyTalk validates the existing AWS credential chain and writes the
selected TinyTalk slot.

This document replaces a GitHub issue for this piece of work and is the source of truth for scope,
security boundaries, acceptance checks, and verification.

## Installed-first choose-model follow-up

The 2026-07-14 follow-up supersedes only the earlier packaging and default import-choice clauses:

1. Every supported install artifact includes boto3/botocore. The release binary must not download a
   Bedrock add-on during `tt auth`; the installer-level binary is already Bedrock-capable.
2. When Claude Code Bedrock settings are detected, the first choice reuses only their non-secret
   AWS region/profile and discovers models live. It must not silently select Claude Code's current
   Opus 4.8 model. Exact-model reuse remains an explicit secondary choice for compatibility.
3. Live discovery shows the user selectable active Bedrock inference-profile IDs and foundation
   model IDs. Labels may include AWS names, but the persisted value is the usable model/profile ID.
4. The user-selected model receives a minimal billed Converse validation before it can be saved.
   Failed validation offers a different-model choice without repeating AWS setup.
5. Reconfiguring an occupied slot does not ask the redundant early “Writing a new … will replace”
   confirmation. The final config preview and `Write this to …?` confirmation remain authoritative.
6. A genuinely fresh config continues directly to the primary provider without a slot question.
   Existing multi-slot configs retain their primary/fallback picker.
7. The live model picker is Claude-only. Keep only active inference profiles and foundation models
   whose usable ID contains `anthropic.claude`; never infer Claude membership from a display name.
   The current personal AWS profile validator must include both a Sonnet 5 ID and an Opus 4.8 ID.
   The manual model-ID escape hatch remains available for locked-down or newly launched models.

## Installer first-launch recovery follow-up

The 2026-07-14 rc11 follow-up hardens the release installer after a real install reported
`the installed binary did not run` even though the same installed rc10 launcher ran successfully
immediately afterward:

1. Downloaded bundles are unpacked and validated in a private staging directory. A broken launcher
   never replaces the previous working installation.
2. A first transient `--version` failure is retried once. If both attempts fail, the installer
   prints the launcher/loader output instead of discarding it.
3. The live tree is replaced only after staged validation, and concurrent installers cannot mutate
   the shared installation tree at the same time.
4. Activation or final verification failure restores the previous installation.

The release validator must run the public `curl | sh` path in an isolated home, confirm the bundled
boto3 directory, and print the new TinyTalk version.

Follow-up validator:

- A release-shaped build imports boto3 without any add-on directory.
- An isolated fresh config plus detected Claude AWS settings reaches a live model picker, can choose
  a non-Opus model, validates it, and writes that exact ID without an overwrite prompt.
- The user's real config is not deleted to perform this test.

## User-visible behavior

1. The user runs `tt auth` (or reaches its backend step through `tt setup`) and chooses AWS
   Bedrock.
2. TinyTalk checks the user-level Claude Code settings file.
3. If it contains a complete supported Bedrock configuration, TinyTalk offers two choices:
   reuse the detected Claude Code settings or configure Bedrock manually.
4. Reuse prints a preview containing only region, profile, and model, then asks for explicit import
   confirmation before probing Bedrock with the existing boto3 credential chain.
5. TinyTalk verifies the imported model through the same Bedrock Runtime Converse API used for real
   TinyTalk requests. This minimal completion has a small AWS usage cost.
6. After the existing final write confirmation, TinyTalk stores a normal secret-free Bedrock
   backend in its own `config.toml`.
7. Declining reuse, finding unsupported settings, or choosing manual setup after a failed import
   probe restarts the complete current manual wizard.
8. If validation finds an expired named AWS SSO session, TinyTalk starts the known-safe
   `aws sso login --profile <profile>` argv itself. The AWS CLI opens the browser and prints the
   authorization URL as a fallback. A successful login automatically repeats validation without a
   separate retry selection.

TinyTalk does not continuously synchronize the two files. Import is a one-time copy of non-secret
provider configuration.

## Supported settings

Read only `~/.claude/settings.json`. A supported document must contain an object-shaped `env` and a
truthy `env.CLAUDE_CODE_USE_BEDROCK` (`"1"` or `"true"`, case-insensitive).

| TinyTalk field | Claude Code source | Rule |
|---|---|---|
| `aws_region` | `env.AWS_REGION` | Required non-empty string |
| `aws_profile` | `env.AWS_PROFILE` | Optional non-empty string |
| `model` | `env.ANTHROPIC_MODEL`, then top-level `model` | First concrete Bedrock model ID |

A concrete reusable model must contain `anthropic.`. Application inference-profile ARNs are not
reused until TinyTalk can resolve and persist their backing model family; without that information,
the current provider would silently lose Claude tool-calling and reasoning-effort behavior. Aliases
such as `opus` and `sonnet` are not guessed. Candidate resolution checks `env.ANTHROPIC_MODEL`
first and then top-level `model`; an unsupported first candidate does not prevent a supported second
candidate from being used.

Claude Code's final literal `[1m]` suffix is a semantic extended-context selection, not part of the
Bedrock model ID. TinyTalk does not currently implement Claude Code's 1M-context opt-in. Detection
records the marker and normalizes the underlying model ID, but reuse requires an additional explicit
confirmation explaining that TinyTalk will use the same model with its standard context window.
Declining that lossy import starts the complete manual setup. Only one exact final `[1m]` suffix is
recognized.

Values must be non-empty strings with no leading/trailing whitespace or control characters. If any
required value is absent, has the wrong type, or the model is not concrete, detection returns no
reusable configuration and the existing manual flow remains available. Malformed or unreadable JSON
is treated the same way and must not crash `tt auth`.

`ANTHROPIC_MODEL` takes precedence over top-level `model` because it is an explicit Bedrock model
pin in Claude Code's provider environment. The top-level setting remains the fallback for the
user-level setup produced by Claude Code's Bedrock wizard.

`AWS_REGION` is deliberately required for automatic reuse. Settings relying on
`AWS_DEFAULT_REGION`, an AWS profile's region, or Claude Code's own fallback continue through manual
setup so TinyTalk never guesses which region to persist.

### Runtime endpoint boundary

TinyTalk uses Bedrock Runtime Converse, whereas Claude Code's Bedrock integration uses the Invoke
API. A Claude-compatible custom gateway is therefore not necessarily compatible with TinyTalk, and
an imported endpoint would receive AWS-signed runtime requests. This scope does not import custom
endpoints: when a non-empty `env.ANTHROPIC_BEDROCK_BASE_URL` is present, automatic reuse is
unsupported and the complete manual flow remains available. A future endpoint-import feature must
require HTTPS, explicit non-default confirmation, and a successful signed Converse compatibility
probe.

## Security invariants

- Continue to resolve credentials only through boto3's standard credential chain.
- Never import or persist `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
  `AWS_BEARER_TOKEN_BEDROCK`, or credential-export output.
- Never execute, parse as a shell command, or persist `awsAuthRefresh`, `awsCredentialExport`, or
  any other command found in Claude settings.
- Never write an AWS-related value to TinyTalk's keyring.
- Show only region, profile, model, and the presence of the `[1m]` compatibility warning in the
  import preview. Render values with escaping and reject control characters.
- Credential renewal is interactive and scoped: only an error classified as expired named-profile
  SSO may start `aws sso login --profile <profile>`. TinyTalk passes an argv list without a shell,
  inherits the terminal so the AWS CLI can show its fallback URL, and automatically retries the
  Bedrock probe only after a zero exit status.
- Keep the product boundary intact: TinyTalk never executes commands generated from natural-language
  requests or command strings imported from settings. The fixed AWS CLI SSO login flow above is the
  sole auth-specific exception and is never derived from `awsAuthRefresh` or
  `awsCredentialExport`.

## Code design

### Settings discovery

Add a small immutable value object for the imported region, profile, normalized model, and whether
Claude Code requested `[1m]`.
Implement a side-effect-free loader in `tinytalk/auth.py` (or a focused auth-settings module if the
review finds the auth module too crowded). Give the loader an injectable path so tests use temporary
files rather than the real home directory.

The loader reads JSON using the standard library, selects only the allowlisted keys above, validates
types and Bedrock enablement, records the exact model suffix case, and returns either the value
object or `None`. It must not mutate `os.environ`.

### Wizard integration

Keep `WizardIO` as the public behavior seam. `_setup_bedrock` receives injectable settings and SSO
login runners for tests. When settings are detected, ask whether to reuse them or continue manually.
The default detected-settings choice reuses region/profile, performs live discovery, and lets the
user choose a model. The secondary exact-model reuse choice prints the allowlisted preview and asks
for explicit confirmation before any network probe. An `[1m]` exact reuse requires a second
compatibility confirmation. Both detected-settings paths skip endpoint, region, and profile prompts,
but keep:

- installed Bedrock dependency availability;
- credential error handling and retry/manual/abort behavior;
- Claude capability detection and optional reasoning-effort selection;
- the outer wizard's preview and final write confirmation.

Only the secondary exact-model reuse treats the imported model as authoritative. The default path
combines active system-defined inference profiles with active text foundation models, displays the
usable IDs, and validates the user's selection through `BedrockProvider`. If one discovery API is
denied but the other returns models, its results remain selectable. On selected-model probe failure,
the user may retry, choose another model, or abort without re-entering AWS settings.

When either imported-model validation or manual catalog discovery returns the provider's specific
named-profile SSO-expiry error, run a fixed `aws sso login` argv with inherited stdio. Do not use a
shell and do not execute any command text read from Claude Code. The AWS CLI owns PKCE/OIDC,
automatic browser opening, and fallback URL display. On success, retry the interrupted probe
automatically. If the CLI is missing, exits nonzero, or cannot start, retain the existing
retry/manual/abort recovery choices with a clear error.

### Configuration and provider

No new TinyTalk config schema is needed. Imported values map to the existing `BackendConfig` fields,
and `provider.factory.make_provider` continues to pass them to `BedrockProvider`.

For Python/source, `uv tool install .`, and standalone release installations, boto3 is installed at
the base installation layer. A backend configured by `tt auth` therefore works on the next plain
`tt` invocation without a Bedrock add-on download.

## TDD seams and cases

Tests observe behavior through three existing seams:

1. Settings discovery: a settings file becomes a validated imported configuration or `None`.
2. Bedrock auth wizard: scripted user choices and a fake prober produce a `BackendDraft` or cancel.
3. AWS SSO renewal: an injectable runner receives one profile and returns success or a displayable
   failure; the production runner is covered through the wizard with subprocess replaced.

Work in vertical red-green slices:

1. A complete Claude Code Bedrock settings fixture is discovered with exact field mapping.
2. Candidate precedence and explicit `[1m]` compatibility handling are applied.
3. Disabled, missing, malformed, wrong-typed, whitespace/control-character, incomplete,
   alias-only, application-ARN, and custom-endpoint settings return `None`.
4. Import choice produces a secret-free Bedrock draft and calls the prober with the imported region
   and profile.
5. The concrete model survives into the draft, bypasses catalog discovery, and is tested through a
   fake Converse-compatible runtime probe.
6. Declining import enters the unchanged manual flow.
7. Import probe failures provide retry, complete-manual-restart, abort, and SSO guidance behavior.
8. The resulting draft survives config loading and provider construction without keyring access.
9. Tests make subprocess execution and keyring writes fail if ignored refresh/export/credential
   fields reach either boundary, and assert `os.environ` remains unchanged.
10. Preview ordering and the absence of unexpected manual prompts are asserted.
11. An expired named SSO session starts the browser-capable AWS CLI login, exposes its inherited
    terminal URL fallback, and automatically retries the probe after success.
12. Login startup/nonzero failures remain recoverable, shell execution is impossible, and
    non-SSO credential failures never start login.
13. Project metadata installs boto3 as a core dependency for source and `uv tool` installs while
    retaining an empty compatibility extra for existing `--extra bedrock` commands.
14. Bedrock reasoning uses adaptive fields on supported new models and bounded budgets on legacy
    models. Thinking never enlarges an explicit caller `max_tokens`: when a legacy cap cannot
    satisfy its budget, or any thinking request exceeds the non-streaming limit, the request
    preserves the cap and falls back to normal generation.

Tests must not inspect the real `~/.claude/settings.json`, contact AWS, execute `awsAuthRefresh`, or
start a real browser/login. Subprocess tests replace the AWS CLI boundary.

## Documentation

Update both `README.md` and `README.ko.md`:

- explain that `tt auth` can reuse a complete user-level Claude Code Bedrock setup;
- list which non-secret values are imported;
- explain that AWS credentials remain in the standard chain and are not copied;
- say that refresh/export commands are never executed;
- disclose that reuse performs one minimal billed Converse request;
- explain the explicit `[1m]` standard-context compatibility confirmation;
- document automatic browser-based `aws sso login --profile ...` renewal and URL fallback;
- document that source and `uv tool install .` installs include boto3 without an extra;
- retain the manual Bedrock setup path;
- remove the stale claim that empty discovery offers to collect and keychain-store an access-key
  pair, which stopped being true in #125.

## Out of scope

- Executing `awsAuthRefresh` or `awsCredentialExport` (the fixed TinyTalk-owned AWS SSO argv is not
  one of these imported commands).
- Importing secrets, Bedrock API keys, or auth-skipping gateway configuration.
- Claude managed, project, or local settings layers.
- `CLAUDE_CONFIG_DIR` and non-default settings file locations.
- Custom endpoint import (manual endpoint setup remains supported).
- Bedrock Mantle routing.
- Application inference-profile ARN import.
- Resolving Claude aliases, `modelOverrides`, or model-family default pins.
- Automatic ongoing synchronization.
- Changes to other providers.
- Adding a persistent TinyTalk setting for 1M context.

## Acceptance checks

- [x] A supported user-level Claude Code Bedrock fixture is detected before manual Bedrock prompts.
- [x] Reuse writes only existing TinyTalk Bedrock fields: kind, model, region, optional profile,
      capabilities, and optional effort.
- [x] No secret is copied to `config.toml`, `os.environ`, or the OS keychain.
- [x] `awsAuthRefresh` and `awsCredentialExport` are never executed or persisted.
- [x] A trailing `[1m]` marker is never silently stripped: reuse explains the standard-context
      behavior and requires an additional confirmation before persisting the underlying model ID.
- [x] Application inference-profile ARNs and custom endpoint settings fall back to complete manual
      setup.
- [x] Missing, malformed, disabled, incomplete, or unsupported settings fall back safely to manual
      configuration.
- [x] Declining reuse preserves the current manual wizard.
- [x] Credential failures retain actionable named-profile SSO guidance.
- [x] Imported settings receive a minimal runtime validation through TinyTalk's Converse path before
      the backend is returned.
- [x] English and Korean documentation describe reuse and the security boundary accurately.
- [x] Focused auth/config/provider tests pass.
- [ ] Full pytest, ruff, and formatting checks pass.
- [x] A final code review reports no unresolved correctness, security, or regression findings.
- [x] Source and `uv tool install .` metadata include boto3 without requiring packages from
      `[bedrock]`; the empty compatibility extra preserves existing commands.
- [x] Named-profile SSO expiry opens the AWS CLI browser flow, keeps its URL visible, and
      automatically retries validation after successful login.
- [x] SSO login failure and missing AWS CLI recovery are tested without shell execution.
- [x] Opus 4.8 adaptive thinking works with Converse, while legacy thinking respects tool-choice,
      sampling, token-budget, non-streaming, and explicit caller-cap constraints.
- [x] Standalone release artifacts bundle boto3/botocore and publish no Bedrock add-on.
- [x] Detected AWS region/profile leads to a live user model picker whose default path never
      silently selects the detected Opus model.
- [x] Active inference profiles remain selectable when `ListFoundationModels` is denied.
- [x] Every newly selected model is Converse-validated and a failed model can be replaced without
      repeating AWS setup.
- [x] Occupied-slot reconfiguration has only the final write confirmation, not an early duplicate.
- [x] The live picker contains Claude models only and the personal-profile check includes Sonnet 5
      and Opus 4.8.
- [x] A transient first launcher failure is retried once, while a permanent failure reports the
      loader output and leaves the previous installation working.
- [x] Bundle extraction and validation happen outside the live installation tree under an
      installer lock.
- [x] The public latest-release `curl | sh` path installs and runs rc11 with bundled boto3.

## Implementation order

1. Add discovery tests and the minimal loader/value object.
2. Add wizard reuse tests and integrate the detected settings.
3. Add config/provider integration coverage where existing tests do not already prove the mapping.
4. Update English and Korean documentation.
5. Run focused tests, full suite, lint, and formatting.
6. Run the repository `code-review` process and address findings.
7. Optionally perform a live manual probe with the user's existing SSO profile; never run a generated
   shell command automatically.

## Pre-implementation review

`gpt-5.6-terra` with high reasoning reviewed this document read-only before implementation on
2026-07-13. Its initial verdict was **block implementation**. The review found four material gaps:

1. `[1m]` was incorrectly treated as cosmetic.
2. Application inference-profile ARNs could lose Claude capabilities under TinyTalk's current
   string-based model-family detection.
3. Claude Code custom endpoints target Invoke-compatible behavior while TinyTalk uses Converse, and
   the control-plane probe would not exercise the imported endpoint.
4. Preview ordering and the meaning of manual fallback after an import failure were ambiguous.

This revision addresses those findings with explicit lossy-import consent for `[1m]`, ARN and custom
endpoint rejection, an import-specific minimal Converse probe, and a fully specified
preview/confirm/retry/manual/abort flow. It also adopts the review's control-character, side-effect,
README wording, and test-matrix recommendations.

## Verification

- Focused auth/add-on/config/Bedrock/i18n/installer/version suite: `222 passed`.
- Ruff lint: the full repository passes.
- Ruff formatting: all touched Python files pass. The repository-wide check reports 26 pre-existing
  files outside this change that would be reformatted.
- Full pytest result: `771 passed, 1 skipped, 7 failed`. All seven failures are confined to the
  pre-existing eval/oracle harness and reflect local fixture/tool portability issues (`.env`, BSD
  `date -v`, and `fd` availability), not auth, configuration, or provider behavior.
- Live personal-setting validation: discovery found the current user-level Claude Code Bedrock
  configuration, including its `[1m]` request, and the normalized model completed a minimal real
  Bedrock Runtime Converse probe successfully. No refresh/export command was executed.
- Review remediation: imported preview values are JSON-escaped, SSO guidance uses shell-safe
  argument quoting, and the integration test now exercises settings discovery through provider
  construction while failing on subprocess or keyring access. The final re-review found no
  unresolved correctness, security, regression, or spec-compliance findings.
- Seamless SSO follow-up: `uv run --extra bedrock tt --version` remains compatible, boto3 imports
  without an extra, named-profile SSO starts one fixed-argv AWS CLI browser flow per wizard path,
  inherited stdio preserves the authorization URL, and a successful login automatically retries
  the interrupted probe. Both Standards and Spec re-reviews found no unresolved findings.
- Installed-tool smoke: after `uv tool install --force .`, plain `tt "show me disk usage"` completed
  through the imported `us.anthropic.claude-opus-4-8` backend and returned `du -sh *` without
  executing it. The provider uses adaptive thinking for Opus 4.8; legacy models receive only
  non-streaming-safe budgets, and no thinking mode silently enlarges an explicit output cap.
- Installed-first choose-model follow-up: a release-shaped PyInstaller build ran as `tt 0.2.0rc9`
  with boto3 inside `_internal/` and no Bedrock add-on. The reinstalled local tool discovered 61
  active system inference profiles through the user's existing AWS profile, including 59 choices
  other than Opus 4.8. The role denied `ListFoundationModels`, proving the profile-only fallback.
  An isolated fresh-config integration test selected and wrote Sonnet 4.6 without either an
  occupied-slot prompt or an early overwrite confirmation; the user's real config was untouched.
- Claude-only discovery follow-up: the personal AWS profile returned 23 selectable Claude IDs after
  filtering. Both regional and global IDs were present for `claude-sonnet-5` and
  `claude-opus-4-8`; all retained IDs contained `anthropic.claude`. The manual model-ID escape hatch
  remains available.
- Release verification: commit `9a3a108` shipped as latest release `v0.2.0rc10`. The release workflow
  built and smoke-tested Linux x86_64, Linux arm64, and macOS arm64 artifacts, and the installer
  workflow passed its unit, bash, and zsh jobs on Linux and macOS. Running the README `curl | sh`
  command against a fully isolated home downloaded the latest release, verified its checksum,
  printed `tt 0.2.0rc10`, and found bundled boto3 under `_internal/boto3`.
- Installer recovery release: commit `b0df513` shipped as latest release `v0.2.0rc11`. All Linux
  and macOS release builds and installer jobs passed. The public README `curl | sh` command installed
  rc11 into an isolated home, verified the checksum, printed `tt 0.2.0rc11`, found bundled boto3,
  and left no installer lock behind.

## References

- `tinytalk/auth.py`
- `tinytalk/config.py`
- `tinytalk/provider/bedrock.py`
- `tinytalk/provider/factory.py`
- `tests/test_auth.py`
- `tests/test_bedrock.py`
- GitHub #124 and #125
- Claude Code on Amazon Bedrock: <https://code.claude.com/docs/en/amazon-bedrock>
- Boto3 credentials: <https://docs.aws.amazon.com/boto3/latest/guide/credentials.html>
