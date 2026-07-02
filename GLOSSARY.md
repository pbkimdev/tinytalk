# GLOSSARY

Shared vocabulary for TinyTalk. Issues, code comments, and docs use these terms as defined here.

- **prompt mode** (also *AI mode*) — the shell state entered by pressing `?` on an empty line
  (and left with `?` or Backspace on an empty line, or by submitting). The prompt gains the
  animated badge; what you type is sent to `tt` on Enter, and the generated command replaces the
  editing buffer for review. Implemented in `tinytalk/shell/tt.zsh` (`_TT_AI_MODE`).
- **badge** — the `TinyTalk` label shown in `PREDISPLAY` while in prompt mode, with a spectrum
  wave animating through its letters (one `region_highlight` span per letter, advanced by a
  ~100ms ticker).
- **slot** — one of the exactly two backends `tt auth` manages: **primary** (`defaults.backend`,
  asked first) and **fallback** (`defaults.escalation_backend`, used when the primary fails).
  Hand-written backends under other names remain valid config but are not wizard-managed (#78).
- **backend** — one `[backends.<name>]` table in `config.toml`: a provider kind plus model and
  credentials. `kind` is the wire protocol (e.g. `openai-compat`); the *alias* shown in the
  wizard is the provider (e.g. `cerebras`), derived or set via the optional `alias` key (#80).
- **widget** — the zsh ZLE integration (`tt init zsh` / `tinytalk/shell/tt.zsh`); "widget mode"
  output is the `tt --widget` eval-able contract it consumes.
