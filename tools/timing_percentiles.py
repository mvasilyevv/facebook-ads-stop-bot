#!/usr/bin/env python3
"""Агрегирует OBSERVER_TIMING-строки из лога: p50/p95/p99/max по каждой метрике.

Использование:
    grep OBSERVER_TIMING logs/observer.log | python3 tools/timing_percentiles.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict

_KEY_VALUE = re.compile(r"(\w+)=(\d+(?:\.\d+)?)")
_SKIP = {"cycle", "passes", "rows"}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(len(sorted_values) * p))
    return sorted_values[index]


def main() -> None:
    buckets: dict[str, list[float]] = defaultdict(list)
    for line in sys.stdin:
        if "OBSERVER_TIMING" not in line:
            continue
        for key, raw in _KEY_VALUE.findall(line):
            if key in _SKIP:
                continue
            try:
                buckets[key].append(float(raw))
            except ValueError:
                pass

    header = f"{'metric':<28}{'n':>6}{'p50':>10}{'p95':>10}{'p99':>10}{'max':>10}"
    print(header)
    print("-" * len(header))
    for key, values in sorted(buckets.items()):
        print(
            f"{key:<28}{len(values):>6}"
            f"{_pct(values, 0.5):>10.0f}"
            f"{_pct(values, 0.95):>10.0f}"
            f"{_pct(values, 0.99):>10.0f}"
            f"{max(values):>10.0f}"
        )


if __name__ == "__main__":
    main()
