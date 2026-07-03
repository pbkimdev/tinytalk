# B1 — Golden suite v2: 25 parallel EN/KO pairs

Spec for the bench PRD sub-issue B1. Parent: #90. Related: #73 (supersedes its
"add 3–5 Korean eval prompts" bullet).

## Goal

Turn the 25-prompt English suite (`tinytalk/eval/suite.py`) into 25 **golden targets**, each carried
by two prompts — one natural English, one natural Korean — sharing a single assertion set, so a
model's EN↔KO score gap is attributable to language alone.

## Design

### Data model

`EvalPrompt` gains two fields (defaults keep all existing call sites valid):

```python
@dataclass(frozen=True)
class EvalPrompt:
    id: str                      # "<target>-en" | "<target>-ko"
    text: str
    assertions: tuple[str, ...]
    expected_danger: str = "safe"
    lang: str = "en"             # "en" | "ko"
    target: str = ""             # pair key, e.g. "disk-usage-top"
```

`SUITE` is generated from a private `_TARGETS` table so the pairing is structural, not conventional:

```python
_TARGETS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    # (target, en_text, ko_text, assertions, expected_danger)
    ("disk-usage-top",
     "where's all my disk space going? biggest folders here first, in sizes I can read",
     "디스크 용량 어디서 다 먹는지 좀 보여줘. 여기서 제일 큰 폴더부터, 사람이 읽을 수 있는 단위로",
     ("uses:du", "pipes_to:sort"), "safe"),
    ...
)
SUITE = tuple(
    EvalPrompt(f"{t}-{lang}", text, asserts, danger, lang=lang, target=t)
    for (t, en, ko, asserts, danger) in _TARGETS
    for lang, text in (("en", en), ("ko", ko))
)  # 50 prompts
```

### Prompt style rules (both languages)

- Written the way a person actually types at a shell: rough outcome descriptions, hedges, no flag
  names, no tool names unless a human would naturally say them ("zip this up" is fine).
- Korean texts are **native phrasings**, not translations — how a Korean speaker would ask for the
  same outcome (spoken register, particles dropped where natural).
- Assertions stay on the *command*, so they are language-independent by construction (per #73's
  finding). Assertion sets are reviewed once per target, not per language.

### Migration of the existing 25

- The 25 existing targets and their assertion sets are kept (ids keep their current stem;
  `disk-usage-top` → `disk-usage-top-en` / `disk-usage-top-ko`).
- EN texts get a naturalness pass: reword any prompt that reads like a spec ("human readable,
  sorted largest first, top 20") into how a person would say it; assertions unchanged.
- `--prompts` filtering keeps working with full ids; it additionally accepts a bare target
  (`disk-usage-top`) which selects both languages.

### Scoring additions

- `PromptResult` is unchanged here (B2 adds token fields); the runner copies `lang`/`target` into
  each result row so exports are self-describing.
- `BackendReport` gains:
  - `strict_pass_pct` — % of results with `format_ok and parses and binaries_exist and
    assertions_pass` (the benchmark headline, per PRD),
  - `strict_pass_pct_en` / `strict_pass_pct_ko` — same, filtered by `lang`.
- `render_leaderboard` ranks by `strict_pass_pct` (tiebreak: cost) and adds `pass`, `EN`, `KO`
  columns; existing columns stay.

## Out of scope
- Cached-token fields, warmup, cost changes (B2).
- HTML rendering (B3).
- The system-prompt rewording from #73 ("natural-language request", same-language explanation) —
  complementary, tracked there.

## Done when
- **unit** (`tests/test_eval.py`): suite integrity — exactly 25 targets × 2 langs, unique ids, each
  pair shares identical `assertions` and `expected_danger`; bare-target prompt filtering; per-lang
  pct properties computed from a fixture report.
- **manual**: a native read-through of all 25 KO prompts confirms they sound like real requests
  (reviewer: Paul).
- `uv run tt eval --backends <any> --prompts disk-usage-top` runs exactly 2 prompts.
