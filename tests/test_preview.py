"""Eval-only fixture file previews."""

from __future__ import annotations

from tinytalk.eval.preview import build_file_preview


def test_awk_group_sum_preview_includes_sales_header():
    preview = build_file_preview("awk-group-sum")
    assert "Working-directory file previews (read-only context):" in preview
    assert "--- sales.csv | CSV, comma-delimited, 3 cols, header: region,product,amount" in preview
    assert "region,product,amount" in preview


def test_csv_quoted_join_preview_flags_quoted_commas():
    preview = build_file_preview("csv-quoted-join")
    assert "customers.csv" in preview
    assert "orders.csv" in preview
    assert "quoted delimiter: yes" in preview
    assert 'c1,"Ada, Lovelace",ada@example.com' in preview


def test_no_input_target_returns_empty_preview():
    assert build_file_preview("k8s-old-tls-secrets") == ""
