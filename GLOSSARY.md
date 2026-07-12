# TinyTalk glossary

Use these terms consistently in issues, code, tests, and documentation. User-facing copy may explain
a term in plainer language, but should not give it a different meaning.

## Shell experience

- **prompt mode** — The zsh state entered by pressing `?` on an empty command line. The user writes a
  natural-language request; Enter sends it to `tt`; a validated command returns to the editing buffer.
  Prompt mode ends after submission, or when the user presses `?` or Backspace on an empty prompt.
- **badge** — The animated `TinyTalk` label shown while prompt mode is active. It signals that typed
  text is a request, not shell code.
- **widget** — The zsh ZLE integration printed by `tt init zsh` and implemented in
  `tinytalk/shell/tt.zsh`. It owns prompt mode, streaming preview, final buffer insertion, danger
  treatment, and recall.
- **editing buffer** — The command line currently being edited by the shell. TinyTalk returns a
  command here; it does not submit the buffer for execution.
- **streaming preview** — Best-effort partial command text shown while a provider streams. Preview
  text never becomes runnable shell input. Only the final validated command can enter the buffer.
- **recall** — Loading a previous TinyTalk command with Up or Down in prompt mode. Destructive history
  entries are commented out again before they return to the buffer.

## Providers and routing

- **backend** — One `[backends.<name>]` table in `config.toml`: a provider protocol (`kind`), model,
  and provider-specific settings. The table name is a user-defined alias.
- **provider** — The external model path represented by a backend: for example Claude Agent SDK,
  Codex Agent SDK, AWS Bedrock, Azure OpenAI, or an OpenAI-compatible HTTP server.
- **kind** — The wire or SDK implementation selected by a backend. Valid kinds are
  `openai-compat`, `anthropic-compat`, `claude-agent-sdk`, `codex-agent-sdk`, `bedrock`, and
  `azure-openai`.
- **slot** — One of the two backend entries managed by `tt auth`: **primary** or **fallback**. The
  primary maps to `defaults.backend`; the fallback maps to `defaults.escalation_backend`. Handwritten
  backend aliases remain valid but are not additional wizard-managed slots.
- **primary** — The backend asked first after an exact-cache miss.
- **fallback** — The optional backend used for the enriched second attempt. It is also used when the
  primary provider fails before returning a valid suggestion.
- **add-on** — A version-matched dependency downloaded on first setup for release builds that keep a
  heavy provider outside the base binary. TinyTalk currently uses add-ons for Bedrock and the Claude
  CLI used by the Claude Agent SDK.

## Generation and validation

- **suggestion** — The model's structured candidate: one command, one explanation, a claimed danger
  level, and optional grounding needs. It is not user-visible until validation passes.
- **grounding** — Facts about the current execution environment supplied to generation and
  validation: OS, shell, installed binaries, selected versions, and on-demand tool help.
- **grounding snapshot** — The persistent cached representation of those host facts. `tt ground`
  inspects it; `tt ground --refresh` rebuilds it.
- **validation ladder** — The ordered checks applied to a suggestion: syntax parse, binary presence,
  best-effort long-flag validation, selected native dry-runs, and danger classification.
- **danger** — The final `safe`, `caution`, or `destructive` classification. Validation may raise the
  model's claimed level but never lower it.
- **native dry-run** — A real command invocation using the tool's own no-op flag, limited to a small
  allowlist. The mutating form is never run by validation.
- **tier** — The route that produced a result: **T0** exact-cache hit, **T1** primary grounded ask, or
  **T2** enriched retry, using the fallback when configured.
- **prompt surface** — Every string a product model can receive: static instructions, user-message
  templates, grounding, validation feedback, and tool descriptions. The source of truth is
  `tinytalk/prompts.py`; `tt prompt` renders the assembled surface without a model call.
- **session context** — Optional, caller-supplied text in `TT_SESSION_CONTEXT`. TinyTalk redacts it
  before including it in the model request.

## Evaluation

- **suite** — The fixed set of English/Korean prompt pairs and deterministic assertions in
  `tinytalk/eval/suite.py`.
- **strict pass** — An eval result whose response format is valid, command parses, referenced
  binaries exist, and all target assertions pass. It does not mean the product executed the command.
- **execution oracle** — A fixture-backed evaluator that runs a recorded command inside an isolated
  temporary sandbox and compares output or filesystem state with the target's expected result.
- **oracle pass** — The execution oracle's independent verdict. It is reported alongside strict pass,
  never merged into the strict-pass definition.
- **delivery** — Whether a backend returned a usable structured response before command validation.
- **stability run** — Repeated generation over the same suite used to measure command and verdict
  flips. It measures model/runtime variability; it does not change recorded results.
