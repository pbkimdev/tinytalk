"""Deterministic stub provider — the issue's "stub backend"."""

from __future__ import annotations

from collections.abc import Callable

from tinytalk.provider.base import Capabilities, Completion, CompletionRequest


class StubProvider:
    """Pops scripted `Completion`s in order (or computes them from the request).

    Construct with either a list of canned `Completion`s, or a callable mapping
    `(request, attempt) -> Completion`. Records every request it saw.
    """

    name = "stub"

    def __init__(
        self,
        capabilities: Capabilities,
        completions: list[Completion] | Callable[[CompletionRequest, int], Completion],
    ):
        self.capabilities = capabilities
        self._completions = completions
        self.requests: list[CompletionRequest] = []
        self._i = 0

    async def complete(self, request: CompletionRequest) -> Completion:
        self.requests.append(request)
        if callable(self._completions):
            completion = self._completions(request, self._i)
        else:
            completion = self._completions[self._i]
        self._i += 1
        return completion
