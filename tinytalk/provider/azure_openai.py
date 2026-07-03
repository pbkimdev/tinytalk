"""Azure OpenAI adapter (PRD-provider-setup.md §4).

Azure OpenAI's chat-completions payload/response shape is identical to OpenAI's own API
— only the URL (`{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=`)
and the auth header (`api-key`, not `Authorization: Bearer`) differ. This subclasses
`OpenAICompatProvider` and overrides just those two things, so no new dependency is
needed — Azure OpenAI is reachable over plain HTTP via the existing `httpx` client.

There is no key-only endpoint to list a resource's deployments (Azure retired the old
one; true enumeration needs the management-plane SDK + AAD auth) — the deployment name
is always typed, never discovered.
"""

from __future__ import annotations

import httpx

from tinytalk.provider.base import Capabilities
from tinytalk.provider.openai_compat import OpenAICompatProvider


class AzureOpenAIProvider(OpenAICompatProvider):
    """`Provider` over an Azure OpenAI deployment (same wire format as openai-compat)."""

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str,
        *,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
        default_effort: str | None = None,
    ):
        super().__init__(
            endpoint,
            deployment,
            api_key=api_key,
            capabilities=capabilities,
            timeout=timeout,
            client=client,
            default_effort=default_effort,
        )
        self.name = f"azure-openai:{deployment}"
        self._api_version = api_version

    def _url(self) -> str:
        return (
            f"{self.base_url}/openai/deployments/{self.model}/chat/completions"
            f"?api-version={self._api_version}"
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key and self._api_key.strip():
            headers["api-key"] = self._api_key
        return headers
