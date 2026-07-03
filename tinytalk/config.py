"""Config loader for `~/.config/tinytalk/config.toml` (#30, PRD §10).

Loads and validates the user config with stdlib `tomllib`. A missing or invalid
config fails with a `ConfigError` whose message says exactly what to fix and
where the file was expected.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_KINDS = (
    "openai-compat",
    "anthropic-compat",
    "claude-agent-sdk",
    "codex-agent-sdk",
    "bedrock",
    "azure-openai",
)
VALID_POSTURES = ("local", "hybrid", "cloud")
VALID_CAPABILITIES = ("tool_calling", "native_json", "grammar")
VALID_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

_EXAMPLE = """\
[defaults]
backend = "local"

[backends.local]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "qwen3:8b"
"""


class ConfigError(Exception):
    """Raised when the config file is missing or invalid."""


@dataclass(frozen=True)
class BackendConfig:
    name: str
    kind: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    capabilities: tuple[str, ...] = ()
    keyring_account: str | None = None  # OS-keychain lookup, tried when api_key_env is unset/empty
    effort: str | None = None  # passed through as reasoning_effort; see VALID_EFFORTS
    aws_region: str | None = None  # bedrock only
    aws_profile: str | None = None  # bedrock only
    azure_api_version: str | None = None  # azure-openai only

    @property
    def api_key(self) -> str | None:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value
        if self.keyring_account:
            import keyring

            return keyring.get_password("tinytalk", self.keyring_account)
        return None


@dataclass(frozen=True)
class Price:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_input_per_mtok: float = 0.0  # 0 ⇒ cached tokens billed at the input rate
    cache_write_per_mtok: float = 0.0  # 0 ⇒ cache-write tokens billed at the input rate


@dataclass(frozen=True)
class Config:
    default_backend: str
    backends: dict[str, BackendConfig]
    posture: str = "local"
    escalation_backend: str | None = None
    cache_enabled: bool = True
    cache_dir: Path | None = None
    prices: dict[str, Price] = field(default_factory=dict)

    def backend(self, name: str | None = None) -> BackendConfig:
        chosen = name or self.default_backend
        if chosen not in self.backends:
            known = ", ".join(sorted(self.backends)) or "(none)"
            raise ConfigError(f"unknown backend {chosen!r}; defined backends: {known}")
        return self.backends[chosen]

    def price(self, model: str) -> Price:
        return self.prices.get(model, Price())


def default_config_path() -> Path:
    if env := os.environ.get("TT_CONFIG"):
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(xdg).expanduser() / "tinytalk" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load and validate the config, or raise `ConfigError` with an actionable message."""
    path = path or default_config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(
            f"no config found at {path}\nRun `tt auth` to set one up, or create it by "
            f"hand — a minimal example:\n\n{_EXAMPLE}"
        ) from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return _validate(data, path)


def _validate(data: dict, path: Path) -> Config:
    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or not isinstance(defaults.get("backend"), str):
        raise ConfigError(f'{path}: [defaults] must set backend = "<name>"\nExample:\n\n{_EXAMPLE}')

    posture = defaults.get("posture", "local")
    if posture not in VALID_POSTURES:
        raise ConfigError(
            f"{path}: [defaults] posture must be one of {', '.join(VALID_POSTURES)}; "
            f"got {posture!r}"
        )

    raw_backends = data.get("backends")
    if not isinstance(raw_backends, dict) or not raw_backends:
        raise ConfigError(f"{path}: define at least one [backends.<name>] table")
    backends = {name: _validate_backend(name, entry, path) for name, entry in raw_backends.items()}

    default_backend = defaults["backend"]
    if default_backend not in backends:
        known = ", ".join(sorted(backends))
        raise ConfigError(
            f"{path}: [defaults] backend {default_backend!r} is not defined; "
            f"defined backends: {known}"
        )
    escalation = defaults.get("escalation_backend")
    if escalation is not None and escalation not in backends:
        known = ", ".join(sorted(backends))
        raise ConfigError(
            f"{path}: [defaults] escalation_backend {escalation!r} is not defined; "
            f"defined backends: {known}"
        )

    cache = data.get("cache", {})
    if not isinstance(cache, dict):
        raise ConfigError(f"{path}: [cache] must be a table")
    cache_dir = Path(cache["dir"]).expanduser() if isinstance(cache.get("dir"), str) else None

    return Config(
        default_backend=default_backend,
        backends=backends,
        posture=posture,
        escalation_backend=escalation,
        cache_enabled=bool(cache.get("enabled", True)),
        cache_dir=cache_dir,
        prices=_validate_prices(data.get("prices", {}), path),
    )


def _validate_backend(name: str, entry: object, path: Path) -> BackendConfig:
    where = f"{path}: [backends.{name}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")

    kind = entry.get("kind")
    if kind not in VALID_KINDS:
        raise ConfigError(f"{where} kind must be one of {', '.join(VALID_KINDS)}; got {kind!r}")

    model = entry.get("model")
    if not isinstance(model, str) or not model:
        raise ConfigError(f'{where} must set model = "<model-id>"')

    base_url = entry.get("base_url")
    if kind in ("openai-compat", "azure-openai") and (
        not isinstance(base_url, str) or not base_url
    ):
        raise ConfigError(f"{where} kind {kind} requires base_url")

    capabilities = entry.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        raise ConfigError(f"{where} capabilities must be a list of strings")
    for cap in capabilities:
        if cap not in VALID_CAPABILITIES:
            raise ConfigError(
                f"{where} unknown capability {cap!r}; valid: {', '.join(VALID_CAPABILITIES)}"
            )

    api_key_env = entry.get("api_key_env")
    if api_key_env is not None and not isinstance(api_key_env, str):
        raise ConfigError(f"{where} api_key_env must be a string")

    keyring_account = entry.get("keyring_account")
    if keyring_account is not None and not isinstance(keyring_account, str):
        raise ConfigError(f"{where} keyring_account must be a string")

    effort = entry.get("effort")
    if effort is not None and effort not in VALID_EFFORTS:
        raise ConfigError(f"{where} unknown effort {effort!r}; valid: {', '.join(VALID_EFFORTS)}")

    aws_region = entry.get("aws_region")
    if aws_region is not None and not isinstance(aws_region, str):
        raise ConfigError(f"{where} aws_region must be a string")
    if kind == "bedrock" and not aws_region:
        raise ConfigError(f"{where} kind bedrock requires aws_region")

    aws_profile = entry.get("aws_profile")
    if aws_profile is not None and not isinstance(aws_profile, str):
        raise ConfigError(f"{where} aws_profile must be a string")

    azure_api_version = entry.get("azure_api_version")
    if azure_api_version is not None and not isinstance(azure_api_version, str):
        raise ConfigError(f"{where} azure_api_version must be a string")
    if kind == "azure-openai" and not azure_api_version:
        raise ConfigError(f"{where} kind azure-openai requires azure_api_version")

    return BackendConfig(
        name=name,
        kind=kind,
        model=model,
        base_url=base_url if isinstance(base_url, str) else None,
        api_key_env=api_key_env,
        capabilities=tuple(capabilities),
        keyring_account=keyring_account,
        effort=effort,
        aws_region=aws_region,
        aws_profile=aws_profile,
        azure_api_version=azure_api_version,
    )


def _validate_prices(raw: object, path: Path) -> dict[str, Price]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [prices] must be a table of per-model tables")
    prices: dict[str, Price] = {}
    for model, entry in raw.items():
        where = f'{path}: [prices."{model}"]'
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        try:
            prices[model] = Price(
                input_per_mtok=float(entry.get("input_per_mtok", 0.0)),
                output_per_mtok=float(entry.get("output_per_mtok", 0.0)),
                cached_input_per_mtok=float(entry.get("cached_input_per_mtok", 0.0)),
                cache_write_per_mtok=float(entry.get("cache_write_per_mtok", 0.0)),
            )
        except (TypeError, ValueError):
            raise ConfigError(f"{where} prices must be numbers") from None
    return prices
