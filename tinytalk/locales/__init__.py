"""Per-language message catalogs for `tinytalk.i18n` (#74).

One module per language (`ko.py`, ...), each a plain `MESSAGES: dict[str, str]` keyed
by the English source string. `tests/test_i18n.py` asserts every catalog covers exactly
the strings extracted with `_()`/`N_()` — add or drop a string in code and the matching
catalog entry must follow.
"""
