"""`tt auth` — interactive provider setup wizard (PRD-provider-setup.md §7).

Picks one provider kind, authenticates against it using that provider's own idiom, tests
the credential with one real call (list-models where the provider has one, a minimal
completion otherwise — on failure the actual error is shown and the user chooses retry
or abort), lists the models actually available where discovery exists, and — after an
explicit confirm — writes a validated backend into `config.toml` via a `tomlkit`
read-modify-write (so hand-added tables, comments, and ordering elsewhere in the file
survive untouched). Secrets are stored in the OS keychain only after that confirm, so an
aborted run never leaves a key behind.

The interactive prompting (`WizardIO`) is a small seam so the decision logic — kind to
config-field mapping, model/effort resolution, the TOML merge — is unit-testable with a
scripted fake, while the real `questionary`-backed prompts are exercised by hand (DoD
§8: scripting real keypresses against an interactive library isn't practical). Every
`questionary`/`keyring`/adapter import here is lazy, matching the cold-start discipline
applied everywhere else — `tt auth` is a deliberately occasional, heavier codepath,
but the module itself must stay cheap to import for `--version`/`--help`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

KIND_CHOICES = [
    ("openai-compat", "OpenAI-compatible HTTP API (OpenAI itself, Ollama, llama.cpp, ...)"),
    ("anthropic-compat", "Anthropic Messages API (raw HTTP, not the Agent SDK)"),
    ("claude-agent-sdk", "Claude Agent SDK (Claude Code login or ANTHROPIC_API_KEY)"),
    ("codex-agent-sdk", "OpenAI Codex Agent SDK (local codex CLI login)"),
    ("bedrock", "AWS Bedrock (uses your AWS credentials)"),
    ("azure-openai", "Azure OpenAI (endpoint + API key)"),
]

_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_CLAUDE_CURATED_MODELS = ("claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5")
_CUSTOM_MODEL = "__custom__"
_NO_EFFORT = "__none__"


class WizardIO(Protocol):
    def select(self, message: str, choices: list[tuple[str, str]]) -> str | None:
        """`choices` is a list of (value, label); returns the chosen value, or None if cancelled."""
        ...

    def text(self, message: str, default: str = "") -> str | None: ...

    def password(self, message: str) -> str | None: ...

    def confirm(self, message: str, default: bool = True) -> bool | None: ...


class QuestionaryIO:
    """`WizardIO` backed by real `questionary` prompts."""

    def select(self, message: str, choices: list[tuple[str, str]]) -> str | None:
        import questionary

        return questionary.select(
            message,
            choices=[questionary.Choice(title=label, value=value) for value, label in choices],
        ).ask()

    def text(self, message: str, default: str = "") -> str | None:
        import questionary

        return questionary.text(message, default=default).ask()

    def password(self, message: str) -> str | None:
        import questionary

        return questionary.password(message).ask()

    def confirm(self, message: str, default: bool = True) -> bool | None:
        import questionary

        return questionary.confirm(message, default=default).ask()


@dataclass
class BackendDraft:
    fields: dict
    secret: str | None = None  # stored via keyring under this backend's name, if set


def run_auth_wizard(config_path: Path, io: WizardIO) -> str | None:
    """Run the wizard; returns the backend name written, or None if the user cancelled."""
    doc = _load_or_new(config_path)
    existing = sorted(doc["backends"].keys()) if "backends" in doc else []

    name: str | None = None
    if existing:
        action = io.select(
            f"Configured backends: {', '.join(existing)}.",
            [
                ("add", "Add a new backend"),
                ("replace", "Replace an existing backend"),
                ("primary_fallback", "Set which backend is primary/fallback"),
                ("exit", "Exit"),
            ],
        )
        if action in (None, "exit"):
            return None
        if action == "primary_fallback":
            return _set_primary_fallback(doc, config_path, existing, io)
        if action == "replace":
            name = io.select("Replace which backend?", [(n, n) for n in existing])
            if name is None:
                return None

    if name is None:
        name = io.text("Name for this backend:", default=_suggest_name(existing))
        if not name:
            return None

    kind = io.select("Provider kind:", KIND_CHOICES)
    if kind is None:
        return None

    draft = _KIND_SETUP[kind](io)
    if draft is None:
        return None

    set_default = (not existing) or bool(
        io.confirm(f"Make {name!r} the default backend?", default=not existing)
    )
    set_fallback = False
    if existing:
        set_fallback = bool(io.confirm(f"Set {name!r} as the fallback backend?", default=False))

    print(f"[backends.{name}]")
    for key, value in draft.fields.items():
        if value:
            print(f"  {key} = {value!r}")
    if draft.secret:
        print("  (API key/credentials → OS keychain, not the file)")
    if not io.confirm(f"Write this to {config_path}?", default=True):
        return None

    if draft.secret:
        _store_secret(name, draft.secret)
        draft.fields["keyring_account"] = name

    _write_backend(doc, name, draft.fields, set_default=set_default, set_fallback=set_fallback)
    _save(config_path, doc)
    return name


def _set_primary_fallback(doc, config_path: Path, existing: list[str], io: WizardIO) -> str | None:
    default = io.select("Primary backend:", [(n, n) for n in existing])
    if default is None:
        return None
    fallback_choices = [(_NO_EFFORT, "(none)")] + [(n, n) for n in existing if n != default]
    fallback = io.select("Fallback backend:", fallback_choices)
    if fallback is None:
        return None

    if "defaults" not in doc:
        import tomlkit

        doc["defaults"] = tomlkit.table()
    doc["defaults"]["backend"] = default
    if fallback != _NO_EFFORT:
        doc["defaults"]["escalation_backend"] = fallback
    elif "escalation_backend" in doc["defaults"]:
        del doc["defaults"]["escalation_backend"]

    _save(config_path, doc)
    return default


def _retry(io: WizardIO) -> bool:
    action = io.select(
        "The credential test failed.",
        [("retry", "Re-enter and try again"), ("abort", "Abort setup")],
    )
    return action == "retry"


# --- per-kind setup steps -----------------------------------------------------------


def _setup_openai_compat(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_openai_compat
    base_url_default = "http://localhost:11434/v1"
    while True:
        base_url = io.text("Base URL:", default=base_url_default)
        if not base_url:
            return None
        api_key = io.password("API key (leave blank for a keyless local server):")
        if api_key is None:
            return None
        api_key = api_key or None
        models, err = probe(base_url, api_key)
        if err is None:
            break
        print(f"tt auth: credential test against {base_url} failed: {err}")
        if not _retry(io):
            return None
        base_url_default = base_url

    model = _pick_model(io, models)
    if model is None:
        return None

    effort = _pick_effort(io, ("low", "medium", "high"))
    capabilities = ["tool_calling", "native_json"] if "api.openai.com" in base_url else []

    fields: dict = {
        "kind": "openai-compat",
        "base_url": base_url,
        "model": model,
        "capabilities": capabilities,
    }
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=api_key)


def _setup_anthropic_compat(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_anthropic_compat
    base_url_default = _DEFAULT_ANTHROPIC_BASE_URL
    while True:
        base_url = io.text("Base URL:", default=base_url_default)
        if not base_url:
            return None
        api_key = io.password("API key:")
        if not api_key:
            return None
        models, err = probe(base_url, api_key)
        if err is None:
            break
        print(f"tt auth: credential test against {base_url} failed: {err}")
        if not _retry(io):
            return None
        base_url_default = base_url

    model_ids = [m["id"] for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]
    model = _pick_model(io, model_ids)
    if model is None:
        return None

    supported_efforts: tuple[str, ...] = ()
    for m in models:
        if isinstance(m, dict) and m.get("id") == model:
            caps = m.get("capabilities") or {}
            supported_efforts = tuple(caps.get("effort") or ())
    effort = _pick_effort(io, supported_efforts or ("low", "medium", "high", "xhigh", "max"))

    fields: dict = {"kind": "anthropic-compat", "model": model}
    if base_url != _DEFAULT_ANTHROPIC_BASE_URL:
        fields["base_url"] = base_url
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=api_key)


def _setup_claude_agent_sdk(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_claude_agent
    print(
        "Auth follows the Claude Agent SDK's own convention: an existing `claude` CLI "
        "login, or ANTHROPIC_API_KEY set in your environment. tt manages no secret here."
    )
    model = _pick_model(io, list(_CLAUDE_CURATED_MODELS))
    if model is None:
        return None
    while True:
        err = probe(model)
        if err is None:
            print("tt auth: Claude Agent SDK test call succeeded.")
            break
        print(f"tt auth: Claude Agent SDK test call failed: {err}")
        print("(log in with `claude` in another terminal, or export ANTHROPIC_API_KEY, then retry)")
        if not _retry(io):
            return None
    effort = _pick_effort(io, ("low", "medium", "high", "xhigh", "max"))
    fields: dict = {"kind": "claude-agent-sdk", "model": model}
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=None)


def _setup_codex_agent_sdk(io: WizardIO, *, prober=None, login=None) -> BackendDraft | None:
    already = io.confirm("Already logged in via the Codex CLI?", default=True)
    if already is None:
        return None
    if not already:
        api_key = io.password(
            "OpenAI API key (persists into the Codex CLI's own login, not stored by tt):"
        )
        if not api_key:
            return None
        try:
            (login or _login_codex)(api_key)
        except Exception as exc:
            print(f"tt auth: codex login failed: {exc}")
            return None

    probe = prober or _probe_codex
    while True:
        models, err = probe()
        if err is None:
            break
        print(f"tt auth: codex model discovery failed: {err}")
        if not _retry(io):
            return None

    model = _pick_model(io, [str(m) for m in models])
    if model is None:
        return None
    effort = _pick_effort(io, ("minimal", "low", "medium", "high", "xhigh"))
    fields: dict = {"kind": "codex-agent-sdk", "model": model}
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=None)


def _setup_bedrock(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_bedrock
    region = io.text("AWS region:", default="us-east-1")
    if not region:
        return None
    profile = io.text("AWS profile (blank = default credential chain):", default="")
    if profile is None:
        return None
    profile = profile or None

    models, err = probe(region, profile, None, None)
    if err is not None:
        print(f"tt auth: bedrock credential test failed: {err}")
    secret = None
    if not models:
        use_explicit = io.confirm(
            "No models discovered with your current AWS credentials. "
            "Enter an access key pair instead?",
            default=False,
        )
        if use_explicit:
            while True:
                access_key_id = io.text("AWS access key ID:")
                secret_access_key = io.password("AWS secret access key:")
                if not access_key_id or not secret_access_key:
                    return None
                models, err = probe(region, profile, access_key_id, secret_access_key)
                if err is None:
                    secret = json.dumps(
                        {
                            "aws_access_key_id": access_key_id,
                            "aws_secret_access_key": secret_access_key,
                        }
                    )
                    break
                print(f"tt auth: bedrock credential test failed: {err}")
                if not _retry(io):
                    return None

    model_ids = [
        m["modelId"] for m in models if isinstance(m, dict) and isinstance(m.get("modelId"), str)
    ]
    model = _pick_model(io, model_ids)
    if model is None:
        return None

    from tinytalk.provider.bedrock import is_claude_model

    is_claude = is_claude_model(model)
    effort = _pick_effort(io, ("low", "medium", "high")) if is_claude else None

    fields: dict = {
        "kind": "bedrock",
        "model": model,
        "aws_region": region,
        "capabilities": ["tool_calling"] if is_claude else [],
    }
    if profile:
        fields["aws_profile"] = profile
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=secret)


def _setup_azure_openai(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_azure_openai
    while True:
        endpoint = io.text("Azure OpenAI endpoint (e.g. https://my-resource.openai.azure.com):")
        if not endpoint:
            return None
        api_version = io.text("API version (e.g. 2026-01-01-preview):")
        if not api_version:
            return None
        deployment = io.text(
            "Deployment name (Azure has no key-only discovery API — type it exactly):"
        )
        if not deployment:
            return None
        api_key = io.password("API key:")
        if not api_key:
            return None
        err = probe(endpoint, deployment, api_version, api_key)
        if err is None:
            print("tt auth: Azure OpenAI test call succeeded.")
            break
        print(f"tt auth: Azure OpenAI test call failed: {err}")
        if not _retry(io):
            return None

    effort = _pick_effort(io, ("low", "medium", "high"))
    fields: dict = {
        "kind": "azure-openai",
        "base_url": endpoint,
        "model": deployment,
        "azure_api_version": api_version,
        "capabilities": [],
    }
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=api_key)


_KIND_SETUP = {
    "openai-compat": _setup_openai_compat,
    "anthropic-compat": _setup_anthropic_compat,
    "claude-agent-sdk": _setup_claude_agent_sdk,
    "codex-agent-sdk": _setup_codex_agent_sdk,
    "bedrock": _setup_bedrock,
    "azure-openai": _setup_azure_openai,
}


# --- credential-test probes (live network/SDK calls) --------------------------------
# Discovery-style probes return (models, error); completion-style probes (kinds with no
# list-models call) return just the error. `None` error means the credential test passed.


def _probe_openai_compat(base_url: str, api_key: str | None) -> tuple[list[str], str | None]:
    import asyncio

    from tinytalk.provider.openai_compat import list_models

    try:
        return asyncio.run(list_models(base_url, api_key=api_key)), None
    except Exception as exc:
        return [], str(exc)


def _probe_anthropic_compat(base_url: str, api_key: str) -> tuple[list[dict], str | None]:
    import asyncio

    from tinytalk.provider.anthropic_compat import list_models

    try:
        return asyncio.run(list_models(base_url, api_key)), None
    except Exception as exc:
        return [], str(exc)


def _probe_codex() -> tuple[list, str | None]:
    from tinytalk.provider.codex_agent import list_models

    try:
        return list_models(), None
    except Exception as exc:
        return [], str(exc)


def _login_codex(api_key: str) -> None:
    from tinytalk.provider.codex_agent import login_api_key

    login_api_key(api_key)


def _probe_bedrock(
    region: str, profile: str | None, access_key_id: str | None, secret_access_key: str | None
) -> tuple[list[dict], str | None]:
    from tinytalk.provider.bedrock import list_foundation_models

    try:
        return (
            list_foundation_models(
                region=region,
                profile=profile,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            ),
            None,
        )
    except Exception as exc:
        return [], str(exc)


def _probe_claude_agent(model: str) -> str | None:
    import asyncio

    from tinytalk.provider.base import CompletionRequest, Message, Role
    from tinytalk.provider.claude_agent import ClaudeAgentProvider

    provider = ClaudeAgentProvider(model=model)
    request = CompletionRequest(
        messages=[Message(Role.USER, "Reply with the word ok.")], max_tokens=8
    )
    try:
        asyncio.run(provider.complete(request))
        return None
    except Exception as exc:
        return str(exc)


def _probe_azure_openai(
    endpoint: str, deployment: str, api_version: str, api_key: str
) -> str | None:
    import asyncio

    from tinytalk.provider.azure_openai import AzureOpenAIProvider
    from tinytalk.provider.base import CompletionRequest, Message, Role

    provider = AzureOpenAIProvider(endpoint, deployment, api_version, api_key=api_key)
    request = CompletionRequest(
        messages=[Message(Role.USER, "Reply with the word ok.")], max_tokens=8
    )
    try:
        asyncio.run(provider.complete(request))
        return None
    except Exception as exc:
        return str(exc)


# --- shared prompt helpers -----------------------------------------------------------


def _pick_model(io: WizardIO, models: list[str]) -> str | None:
    if not models:
        return io.text("Model id (no models discovered — type one):")
    choices = [(m, m) for m in models] + [(_CUSTOM_MODEL, "(type a different model id)")]
    picked = io.select("Model:", choices)
    if picked is None:
        return None
    if picked == _CUSTOM_MODEL:
        return io.text("Model id:")
    return picked


def _pick_effort(io: WizardIO, levels: tuple[str, ...]) -> str | None:
    if not levels:
        return None
    choices = [(_NO_EFFORT, "(default — don't set one)")] + [(lv, lv) for lv in levels]
    picked = io.select("Reasoning effort:", choices)
    if picked in (None, _NO_EFFORT):
        return None
    return picked


def _suggest_name(existing: list[str]) -> str:
    for candidate in ("primary", "backend"):
        if candidate not in existing:
            return candidate
    return f"backend{len(existing) + 1}"


def _store_secret(account: str, value: str) -> None:
    import keyring

    keyring.set_password("tinytalk", account, value)


# --- config.toml read-modify-write (tomlkit; preserves everything else untouched) ---


def _load_or_new(path: Path):
    import tomlkit

    if path.exists():
        return tomlkit.parse(path.read_text())
    return tomlkit.document()


def _write_backend(doc, name: str, fields: dict, *, set_default: bool, set_fallback: bool) -> None:
    import tomlkit

    if "backends" not in doc:
        doc["backends"] = tomlkit.table(is_super_table=True)
    table = tomlkit.table()
    for key, value in fields.items():
        if value:
            table[key] = value
    doc["backends"][name] = table

    if "defaults" not in doc:
        doc["defaults"] = tomlkit.table()
    if set_default:
        doc["defaults"]["backend"] = name
    if set_fallback:
        doc["defaults"]["escalation_backend"] = name


def _save(path: Path, doc) -> None:
    import tomlkit

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc))
