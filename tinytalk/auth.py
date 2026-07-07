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

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tinytalk.config import env_language
from tinytalk.i18n import N_, _

# Labels are N_-marked (extracted into the catalog, translated with `_()` at display
# time) so the module constant keeps the English source text.
KIND_CHOICES = [
    ("openai-compat", N_("OpenAI-compatible HTTP API (OpenAI itself, Ollama, llama.cpp, ...)")),
    ("anthropic-compat", N_("Anthropic Messages API (raw HTTP, not the Agent SDK)")),
    ("claude-agent-sdk", N_("Claude Agent SDK (Claude Code login or ANTHROPIC_API_KEY)")),
    ("codex-agent-sdk", N_("OpenAI Codex Agent SDK (local codex CLI login)")),
    ("bedrock", N_("AWS Bedrock (uses your AWS credentials)")),
    ("azure-openai", N_("Azure OpenAI (endpoint + API key)")),
]

_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_CLAUDE_CURATED_MODELS = ("claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5")
_CUSTOM_MODEL = "__custom__"
_CUSTOM_AWS_PROFILE = "__custom_profile__"
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

        if not default:
            return questionary.text(message).ask()
        from prompt_toolkit.formatted_text import FormattedText

        # The default renders as a dimmed placeholder (typed over, not cleared);
        # a plain Enter accepts it. Cancel is Ctrl-C (None), not an empty submit (#81).
        answer = questionary.text(
            message, placeholder=FormattedText([("fg:#767676", default)])
        ).ask()
        if answer is None:
            return None
        return answer or default

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
    """Run the wizard; returns the slot written ("primary"/"fallback"), or None if cancelled.

    The wizard manages exactly two slots (#78): `primary` (defaults.backend) and
    `fallback` (defaults.escalation_backend). Writing a slot replaces it wholesale.
    """
    doc = _load_or_new(config_path)
    backends = doc["backends"] if "backends" in doc else {}
    defaults = doc["defaults"] if "defaults" in doc else {}

    # Fallback is only offered when the config already has a usable default —
    # otherwise the written file would fail load_config's defaults.backend check.
    default_name = defaults.get("backend")
    if default_name and default_name in backends:
        choices = [
            ("primary", _slot_label("primary", backends)),
            ("fallback", _slot_label("fallback", backends)),
        ]
        if "fallback" in backends or defaults.get("escalation_backend"):
            choices.append(("remove-fallback", _("remove fallback")))
        slot = io.select(_("Which backend do you want to set up?"), choices)
        if slot is None:
            return None
        if slot == "remove-fallback":
            return _remove_fallback(doc, config_path, io)
    else:
        slot = "primary"

    replaced = backends[slot] if slot in backends else None
    if replaced is not None:
        if not io.confirm(
            _("Writing a new {slot} will replace the existing one ({current}). Continue?").format(
                slot=slot, current=_describe(replaced)
            ),
            default=False,
        ):
            return None

    kind = io.select(_("Provider kind:"), [(value, _(label)) for value, label in KIND_CHOICES])
    if kind is None:
        return None

    draft = _KIND_SETUP[kind](io)
    if draft is None:
        return None

    language = None
    if slot == "primary":
        current = str(defaults.get("language") or env_language())
        language = io.text(
            _('Explanation language (code or name, e.g. "en", "ko"):'), default=current
        )
        if language is None:
            return None

    print(f"[backends.{slot}]")
    for key, value in draft.fields.items():
        if value:
            print(f"  {key} = {value!r}")
    if draft.secret:
        print(_("  (API key/credentials → OS keychain, not the file)"))
    if language:
        print(f"  [defaults] language = {language!r}")
    if not io.confirm(_("Write this to {path}?").format(path=config_path), default=True):
        return None

    stale_account = replaced.get("keyring_account") if replaced is not None else None
    if draft.secret:
        _store_secret(slot, draft.secret)
        draft.fields["keyring_account"] = slot

    _write_backend(
        doc, slot, draft.fields, set_default=(slot == "primary"), set_fallback=(slot == "fallback")
    )
    if language:
        doc["defaults"]["language"] = language
    _save(config_path, doc)
    # Cleanup only after the write succeeded, and never while any table still
    # references the account — hand-written backends may share one (#86).
    if stale_account and not _account_referenced(doc, stale_account):
        _delete_secret(stale_account)
    return slot


def _remove_fallback(doc, config_path: Path, io: WizardIO) -> str | None:
    """Retire the fallback: defaults key, slot table, and its (unshared) secret (#86)."""
    backends = doc["backends"] if "backends" in doc else {}
    current = backends["fallback"] if "fallback" in backends else None
    what = _describe(current) if current is not None else _("config entry only")
    if not io.confirm(_("Remove the fallback ({what})?").format(what=what), default=False):
        return None
    stale_account = current.get("keyring_account") if current is not None else None
    if current is not None:
        del doc["backends"]["fallback"]
    if "defaults" in doc and "escalation_backend" in doc["defaults"]:
        del doc["defaults"]["escalation_backend"]
    _save(config_path, doc)
    if stale_account and not _account_referenced(doc, stale_account):
        _delete_secret(stale_account)
    return "fallback"


def _account_referenced(doc, account: str) -> bool:
    backends = doc["backends"] if "backends" in doc else {}
    return any(table.get("keyring_account") == account for table in backends.values())


def _slot_label(slot: str, backends) -> str:
    if slot in backends:
        return f"{slot} — {_describe(backends[slot])}"
    return _("{slot} — (not set)").format(slot=slot)


def _describe(table) -> str:
    return f"{_alias(table)}/{table.get('model', '?')}"


_KIND_ALIASES = {
    "claude-agent-sdk": "claude",
    "codex-agent-sdk": "codex",
    "anthropic-compat": "anthropic",
    "bedrock": "bedrock",
}


def _alias(table) -> str:
    """Short provider name: explicit `alias` key, else derived from kind/base_url (#80)."""
    alias = table.get("alias")
    if alias:
        return alias
    kind = table.get("kind", "?")
    if kind in _KIND_ALIASES:
        return _KIND_ALIASES[kind]
    from urllib.parse import urlsplit

    host = urlsplit(table.get("base_url") or "").hostname or ""
    if _is_local(host):
        return "local"
    labels = host.split(".")
    return labels[-2] if len(labels) >= 2 else kind


def _is_local(host: str) -> bool:
    if host == "localhost":
        return True
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _retry(io: WizardIO) -> bool:
    action = io.select(
        _("The credential test failed."),
        [("retry", _("Re-enter and try again")), ("abort", _("Abort setup"))],
    )
    return action == "retry"


# --- per-kind setup steps -----------------------------------------------------------


def _setup_openai_compat(io: WizardIO, *, prober=None, provisioner=None) -> BackendDraft | None:
    mode = io.select(
        _("Connect an OpenAI-compatible server:"),
        [
            ("managed", _("Set up local Gemma + server for me (recommended)")),
            ("manual", _("I already have a server — enter its base URL")),
        ],
    )
    if mode is None:
        return None
    if mode == "managed":
        if provisioner is None:
            from tinytalk.localsetup import provision_local_backend

            provisioner = provision_local_backend
        try:
            draft = provisioner(io)
        except Exception as exc:  # managed setup is best-effort; never crash the wizard
            print(
                _(
                    "tt auth: managed local setup failed ({error}) — falling back to manual setup."
                ).format(error=exc)
            )
            draft = None
        if draft is not None:
            return draft
        # A declined, unavailable, or failed managed setup falls through to the manual flow.

    return _setup_openai_compat_manual(io, prober=prober)


def _setup_openai_compat_manual(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_openai_compat
    base_url_default = "http://localhost:11434/v1"
    while True:
        base_url = io.text(_("Base URL:"), default=base_url_default)
        if not base_url:
            return None
        api_key = io.password(_("API key (leave blank for a keyless local server):"))
        if api_key is None:
            return None
        api_key = api_key or None
        models, err = probe(base_url, api_key)
        if err is None:
            break
        print(
            _("tt auth: credential test against {base_url} failed: {error}").format(
                base_url=base_url, error=err
            )
        )
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
        base_url = io.text(_("Base URL:"), default=base_url_default)
        if not base_url:
            return None
        api_key = io.password(_("API key:"))
        if not api_key:
            return None
        models, err = probe(base_url, api_key)
        if err is None:
            break
        print(
            _("tt auth: credential test against {base_url} failed: {error}").format(
                base_url=base_url, error=err
            )
        )
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
    from tinytalk.addons import AddonInstallError, install_addon

    try:
        install_addon("claude")
    except AddonInstallError as exc:
        print(f"tt auth: {exc}")
        return None
    probe = prober or _probe_claude_agent
    print(
        _(
            "Auth follows the Claude Agent SDK's own convention: an existing `claude` CLI "
            "login, or ANTHROPIC_API_KEY set in your environment. tt manages no secret here."
        )
    )
    model = _pick_model(io, list(_CLAUDE_CURATED_MODELS))
    if model is None:
        return None
    while True:
        err = probe(model)
        if err is None:
            print(_("tt auth: Claude Agent SDK test call succeeded."))
            break
        print(_("tt auth: Claude Agent SDK test call failed: {error}").format(error=err))
        print(
            _("(log in with `claude` in another terminal, or export ANTHROPIC_API_KEY, then retry)")
        )
        if not _retry(io):
            return None
    effort = _pick_effort(io, ("low", "medium", "high", "xhigh", "max"))
    fields: dict = {"kind": "claude-agent-sdk", "model": model}
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=None)


def _setup_codex_agent_sdk(io: WizardIO, *, prober=None, login=None) -> BackendDraft | None:
    already = io.confirm(_("Already logged in via the Codex CLI?"), default=True)
    if already is None:
        return None
    if not already:
        api_key = io.password(
            _("OpenAI API key (persists into the Codex CLI's own login, not stored by tt):")
        )
        if not api_key:
            return None
        try:
            (login or _login_codex)(api_key)
        except Exception as exc:
            print(_("tt auth: codex login failed: {error}").format(error=exc))
            return None

    probe = prober or _probe_codex
    while True:
        models, err = probe()
        if err is None:
            break
        print(_("tt auth: codex model discovery failed: {error}").format(error=err))
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
    from tinytalk.addons import AddonInstallError, install_addon

    try:
        install_addon("bedrock")
    except AddonInstallError as exc:
        print(f"tt auth: {exc}")
        return None
    probe = prober or _probe_bedrock
    endpoint_url = io.text(_("Custom Bedrock endpoint URL (blank = AWS default):"), default="")
    if endpoint_url is None:
        return None
    endpoint_url = endpoint_url or None
    region = io.text(_("AWS region:"), default="us-east-1")
    if not region:
        return None
    profile = _pick_aws_profile(io)
    if profile is None:
        return None
    profile = profile or None

    while True:
        models, err = probe(endpoint_url, region, profile)
        if err is None:
            break
        print(_("tt auth: bedrock credential test failed: {error}").format(error=err))
        if _looks_like_aws_credential_error(err):
            if profile:
                print(
                    _("tt auth: run `{command}` in another terminal, then choose retry.").format(
                        command=f"aws sso login --profile {profile}"
                    )
                )
            else:
                print(
                    _(
                        "tt auth: fix the standard AWS credential chain "
                        "(env, ~/.aws/credentials, SSO, or IAM role), then choose retry."
                    )
                )
        if not _retry(io):
            models = []
            break

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
    if endpoint_url:
        fields["base_url"] = endpoint_url
    if profile:
        fields["aws_profile"] = profile
    if effort:
        fields["effort"] = effort
    return BackendDraft(fields=fields, secret=None)


def _setup_azure_openai(io: WizardIO, *, prober=None) -> BackendDraft | None:
    probe = prober or _probe_azure_openai
    while True:
        endpoint = io.text(_("Azure OpenAI endpoint (e.g. https://my-resource.openai.azure.com):"))
        if not endpoint:
            return None
        api_version = io.text(_("API version (e.g. 2026-01-01-preview):"))
        if not api_version:
            return None
        deployment = io.text(
            _("Deployment name (Azure has no key-only discovery API — type it exactly):")
        )
        if not deployment:
            return None
        api_key = io.password(_("API key:"))
        if not api_key:
            return None
        err = probe(endpoint, deployment, api_version, api_key)
        if err is None:
            print(_("tt auth: Azure OpenAI test call succeeded."))
            break
        print(_("tt auth: Azure OpenAI test call failed: {error}").format(error=err))
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


def _pick_aws_profile(io: WizardIO) -> str | None:
    profiles = _available_aws_profiles()
    if not profiles:
        return io.text(_("AWS profile (blank = default credential chain):"), default="")

    choices = [
        ("", _("(default AWS credential chain)")),
        *[(profile, profile) for profile in profiles],
        (_CUSTOM_AWS_PROFILE, _("(type a different AWS profile)")),
    ]
    picked = io.select(_("AWS profile:"), choices)
    if picked is None:
        return None
    if picked == _CUSTOM_AWS_PROFILE:
        return io.text(_("AWS profile name (blank = default credential chain):"), default="")
    return picked


def _available_aws_profiles() -> list[str]:
    try:
        from tinytalk.addons import ensure_bedrock_importable

        ensure_bedrock_importable()
        import boto3
    except Exception:
        return []
    try:
        return list(boto3.Session().available_profiles)
    except Exception:
        return []


def _looks_like_aws_credential_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in ("credential", "sso", "token", "nocredentials", "unauthorizedsso")
    )


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
    endpoint_url: str | None, region: str, profile: str | None
) -> tuple[list[dict], str | None]:
    from tinytalk.provider.bedrock import list_foundation_models

    try:
        return (
            list_foundation_models(
                region=region,
                profile=profile,
                endpoint_url=endpoint_url,
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
        model = io.text(_("Model id (no models discovered — type one):"))
        return model or None
    choices = [(m, m) for m in models] + [(_CUSTOM_MODEL, _("(type a different model id)"))]
    picked = io.select(_("Model:"), choices)
    if picked is None:
        return None
    if picked == _CUSTOM_MODEL:
        model = io.text(_("Model id:"))
        return model or None
    return picked


def _pick_effort(io: WizardIO, levels: tuple[str, ...]) -> str | None:
    if not levels:
        return None
    choices = [(_NO_EFFORT, _("(default — don't set one)"))] + [(lv, lv) for lv in levels]
    picked = io.select(_("Reasoning effort:"), choices)
    if picked in (None, _NO_EFFORT):
        return None
    return picked


def _store_secret(account: str, value: str) -> None:
    import keyring

    keyring.set_password("tinytalk", account, value)


def _delete_secret(account: str) -> None:
    import keyring
    import keyring.errors

    try:
        keyring.delete_password("tinytalk", account)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone


# --- config.toml read-modify-write (tomlkit; preserves everything else untouched) ---


def _load_or_new(path: Path):
    import tomlkit

    if path.exists():
        text = path.read_text()
        if text and not text.endswith("\n"):
            text += "\n"
        return tomlkit.parse(text)
    return tomlkit.document()


def _write_backend(doc, name: str, fields: dict, *, set_default: bool, set_fallback: bool) -> None:
    import tomlkit

    backends_existed = "backends" in doc
    if not backends_existed:
        doc["backends"] = tomlkit.table(is_super_table=True)
    table = tomlkit.table()
    for key, value in fields.items():
        if value:
            table[key] = value
    if backends_existed:
        table.add(tomlkit.nl())
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
