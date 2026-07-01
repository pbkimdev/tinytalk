"""Built-in eval harness (#32, PRD §11) — score models on this machine."""

from clite.eval.runner import BackendReport, PromptResult, run_eval
from clite.eval.suite import SUITE, EvalPrompt, check_assertion

__all__ = [
    "SUITE",
    "EvalPrompt",
    "check_assertion",
    "BackendReport",
    "PromptResult",
    "run_eval",
]
