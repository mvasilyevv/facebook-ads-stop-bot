# -*- coding: utf-8 -*-
"""Ожидание опрашивает состояние, а не чинит его.

19.08.2026 деплой не мог пройти шаг канала стола. Внутрь wait_for был передан
ensure_browser_channel — ручка, которая перезапускает профиль Vision. Опрос раз
в пять секунд давал 36 принудительных перезапусков за 180 секунд, и холодный
старт, идущий дольше интервала опроса, не завершался ни при каком пределе
времени. Лечение — действие, его выполняют один раз; ожидание после этого
только читает.

Гард держит соглашение об именах: предикат wait_for обязан называться
require_* или _check_*, и такие функции не выполняют действий.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FBCTL = ROOT / "fbctl"

_ALLOWED_PREFIXES = ("require_", "_check_")


def _wait_for_predicates() -> list[tuple[str, str]]:
    """Пары (файл, имя вызываемого) для каждого предиката, переданного в wait_for."""
    found: list[tuple[str, str]] = []
    for source_file in sorted(_FBCTL.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if not (isinstance(callee, ast.Name) and callee.id == "wait_for"):
                continue
            if len(node.args) < 2:
                continue
            predicate = node.args[1]
            if isinstance(predicate, ast.Lambda):
                body = predicate.body
                if isinstance(body, ast.Call):
                    target = body.func
                    name = (
                        target.attr
                        if isinstance(target, ast.Attribute)
                        else getattr(target, "id", "")
                    )
                    found.append((source_file.name, name))
                    continue
                found.append((source_file.name, ast.dump(body)[:40]))
                continue
            name = (
                predicate.attr
                if isinstance(predicate, ast.Attribute)
                else getattr(predicate, "id", "")
            )
            found.append((source_file.name, name))
    return found


def test_every_wait_predicate_is_read_only() -> None:
    predicates = _wait_for_predicates()
    assert predicates, "не найдено ни одного вызова wait_for — гард смотрит не туда"

    offenders = [
        f"{where}:{name}" for where, name in predicates if not name.startswith(_ALLOWED_PREFIXES)
    ]
    assert offenders == [], (
        "предикат ожидания обязан только читать (require_* или _check_*): "
        + ", ".join(offenders)
        + " — лечащая ручка на месте пробы повторяет лечение каждый интервал опроса"
    )
