# -*- coding: utf-8 -*-
"""Contracts for stop rules labels."""

from __future__ import annotations

import re
from pathlib import Path

from core.rules.labels import RULE_LABELS, RULE_LABELS_SHORT

ROOT = Path(__file__).resolve().parents[2]


def _parse_ts_dict(source: str, dict_name: str) -> dict[str, str]:
    declaration = re.search(
        rf"export const {dict_name}: Record<string, string> = {{(?P<body>.*?)\}};",
        source,
        re.DOTALL,
    )
    assert declaration is not None, f"Could not find {dict_name} in TypeScript source"

    matches = re.findall(r'([a-zA-Z0-9_]+):\s*"([^"]+)"', declaration.group("body"))
    return dict(matches)


def test_rule_labels_match_between_backend_and_shared_ts() -> None:
    source_path = ROOT / "packages/shared/src/constants/rules.ts"
    source = source_path.read_text(encoding="utf-8")

    ts_rule_labels = _parse_ts_dict(source, "RULE_CODE_LABELS")

    # Check that keys match exactly
    assert set(RULE_LABELS.keys()) == set(ts_rule_labels.keys()), (
        "RULE_LABELS keys mismatch between backend and TS"
    )

    # Check that values match exactly for each key
    for key in RULE_LABELS:
        assert RULE_LABELS[key] == ts_rule_labels[key], (
            f"RULE_LABELS value mismatch for {key}: backend '{RULE_LABELS[key]}' != TS '{ts_rule_labels[key]}'"
        )


def test_rule_labels_short_match_between_backend_and_shared_ts() -> None:
    source_path = ROOT / "packages/shared/src/constants/rules.ts"
    source = source_path.read_text(encoding="utf-8")

    ts_rule_labels_short = _parse_ts_dict(source, "RULE_CODE_LABELS_SHORT")

    # Check that keys match exactly
    assert set(RULE_LABELS_SHORT.keys()) == set(ts_rule_labels_short.keys()), (
        "RULE_LABELS_SHORT keys mismatch between backend and TS"
    )

    # Check that values match exactly for each key
    for key in RULE_LABELS_SHORT:
        assert RULE_LABELS_SHORT[key] == ts_rule_labels_short[key], (
            f"RULE_LABELS_SHORT value mismatch for {key}: backend '{RULE_LABELS_SHORT[key]}' != TS '{ts_rule_labels_short[key]}'"
        )
