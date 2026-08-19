#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Печатает, какие области затронуты диапазоном коммитов.

Пропуск проверки по ошибке дороже лишнего прогона, поэтому неопределённость
всегда разрешается в пользу «гоняем всё»: неизвестная база, база из сорока
нулей, недостижимый коммит, сбой git — любой из этих случаев печатает true.

Областей ровно две — ровно столько, сколько проверок в этом репозитории имеют
локализованный вход. Остальные джобы читают весь репозиторий и фильтрации не
подлежат: подробности в docs/superpowers/plans/2026-08-19-pipeline-affected-only.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Префиксы и точные имена, попадание в которые делает область затронутой.
AREAS: dict[str, tuple[str, ...]] = {
    "ui": (
        "frontend/",
        "packages/features/",
        "packages/operator-api/",
        "packages/operator-ui/",
        "packages/shared/",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package.json",
        "tsconfig.base.json",
    ),
    "bundle": (
        "fbctl/",
        "scripts/fbctl",
        "tests/rehearsal/",
        "deploy/compose/",
        "deploy/caddy/",
        "deploy/systemd/caddy-fb-agent-env.conf",
    ),
}

_EMPTY_SHA = "0" * 40


def _print(values: dict[str, bool]) -> None:
    for key in AREAS:
        print(f"{key}={'true' if values.get(key) else 'false'}")


def _changed_files(base: str, head: str) -> list[str] | None:
    """Список изменённых файлов или None, если диапазон неразрешим."""
    if not base or not head or base.strip(_EMPTY_SHA[0]) == "" or base == _EMPTY_SHA:
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}", f"{head}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--assume-all", action="store_true")
    args = parser.parse_args()

    if args.assume_all:
        _print({key: True for key in AREAS})
        return 0

    files = _changed_files(args.base, args.head)
    if files is None:
        print("диапазон коммитов не разрешён — гоним всё", file=sys.stderr)
        _print({key: True for key in AREAS})
        return 0

    _print(
        {key: any(path.startswith(prefixes) for path in files) for key, prefixes in AREAS.items()}
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
