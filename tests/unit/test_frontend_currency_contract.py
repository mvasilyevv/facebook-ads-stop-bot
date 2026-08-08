from __future__ import annotations

import re
from pathlib import Path

from core.money import SUPPORTED_CURRENCY_EXPONENTS


def test_frontend_currency_exponents_match_backend_contract() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "packages/shared/src/format/currencyContract.ts"
    ).read_text(encoding="utf-8")
    frontend: dict[str, int] = {}
    for name, exponent in (
        ("EXPONENT_ZERO", 0),
        ("EXPONENT_TWO", 2),
        ("EXPONENT_THREE", 3),
    ):
        match = re.search(rf"const {name} = \[(.*?)\] as const;", source, re.DOTALL)
        assert match is not None, f"{name} is missing from the frontend contract"
        for code in re.findall(r'"([A-Z]{3})"', match.group(1)):
            assert code not in frontend, f"duplicate frontend currency {code}"
            frontend[code] = exponent

    assert frontend == SUPPORTED_CURRENCY_EXPONENTS
