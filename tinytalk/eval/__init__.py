"""Built-in eval harness (#32, PRD §11) — score models on this machine."""

from tinytalk.eval.runner import BackendReport, PromptResult, run_eval
from tinytalk.eval.suite import SUITE, EvalPrompt, check_assertion

__all__ = [
    "SUITE",
    "EvalPrompt",
    "check_assertion",
    "BackendReport",
    "PromptResult",
    "run_eval",
]
