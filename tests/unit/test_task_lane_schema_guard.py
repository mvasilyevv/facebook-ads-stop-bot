# -*- coding: utf-8 -*-
"""Гард: TASK_LANES в коде обязан совпадать с CHECK-констрейнтом ck_task_queue_lane в БД.

Граница money-смежная: очередь «money» — единственная, которую читает
autopause_worker. Расхождение между Python и схемой может быть:
  • «тихим» (очередь в коде, нет в БД) — runtime-ошибка при постановке задачи;
  • «ещё тише» (очередь в БД, нет в коде) — задачи в неё кладут, но никто не забирает.

Констрейнт читается статически из SQL-файла baseline.
PostgreSQL поднимать не нужно.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.tasks.queue import TASK_LANES

# ── Пути ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_SQL = _REPO_ROOT / "migrations" / "versions" / "0001_safety_first_baseline.sql"

# ── Имя констрейнта ───────────────────────────────────────────────────────────

_CONSTRAINT_NAME = "ck_task_queue_lane"

# ── Явный список исключений (пустой) ─────────────────────────────────────────
#
# Если очередь намеренно есть только с одной стороны, занести сюда с объяснением.
# Пример:
#   ONLY_IN_PYTHON: frozenset = frozenset()   # нет намеренных расхождений
#   ONLY_IN_DB:     frozenset = frozenset()   # нет намеренных расхождений
#
ONLY_IN_PYTHON: frozenset[str] = frozenset()
ONLY_IN_DB: frozenset[str] = frozenset()


# ── Парсер ───────────────────────────────────────────────────────────────────


def _parse_lanes_from_sql(sql: str) -> frozenset[str]:
    """Извлекает значения очередей из CHECK-констрейнта ck_task_queue_lane.

    Ищет строку вида:
        CONSTRAINT ck_task_queue_lane CHECK (((lane)::text = ANY (ARRAY[...])))
    и извлекает строковые литералы из ARRAY[...], устойчиво к PostgreSQL-приведениям
    вида ('money'::character varying)::text.
    """
    # Ищем всю строку с нужным констрейнтом
    pattern = re.compile(
        r"CONSTRAINT\s+" + re.escape(_CONSTRAINT_NAME) + r"\s+CHECK\s*\((.+?)\)\s*[,;)]",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(sql)
    if m is None:
        raise ValueError(
            f"Констрейнт {_CONSTRAINT_NAME!r} не найден в {_BASELINE_SQL}. "
            "Файл переименован или констрейнт удалён — это ошибка."
        )
    constraint_body = m.group(1)

    # Извлекаем строковые литералы: 'money', 'interactive', …
    # Паттерн устойчив к ('money'::character varying)::text и к 'money'
    literals = re.findall(r"'([^']+)'", constraint_body)
    if not literals:
        raise ValueError(
            f"Не удалось извлечь значения из констрейнта {_CONSTRAINT_NAME!r}. "
            f"Тело констрейнта: {constraint_body!r}"
        )
    return frozenset(literals)


# ── Вспомогательный парсер для параметрических тестов ────────────────────────


def _parse_lanes_from_sql_text(sql_text: str) -> frozenset[str]:
    """То же, что _parse_lanes_from_sql, но принимает произвольный SQL-текст."""
    pattern = re.compile(
        r"CONSTRAINT\s+" + re.escape(_CONSTRAINT_NAME) + r"\s+CHECK\s*\((.+?)\)\s*[,;)]",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(sql_text)
    if m is None:
        raise ValueError(f"Констрейнт {_CONSTRAINT_NAME!r} не найден в тексте.")
    constraint_body = m.group(1)
    literals = re.findall(r"'([^']+)'", constraint_body)
    if not literals:
        raise ValueError(f"Не удалось извлечь значения: {constraint_body!r}")
    return frozenset(literals)


# ── Основной гард ─────────────────────────────────────────────────────────────


def test_task_lanes_match_db_constraint() -> None:
    """TASK_LANES и CHECK-констрейнт ck_task_queue_lane обязаны совпадать."""
    assert _BASELINE_SQL.is_file(), (
        f"Baseline-миграция не найдена: {_BASELINE_SQL}. "
        "Файл обязан существовать — это не skippable."
    )

    sql = _BASELINE_SQL.read_text(encoding="utf-8")
    db_lanes = _parse_lanes_from_sql(sql)

    # Учитываем явные исключения
    python_effective = TASK_LANES - ONLY_IN_PYTHON
    db_effective = db_lanes - ONLY_IN_DB

    only_python = python_effective - db_effective
    only_db = db_effective - python_effective

    messages: list[str] = []
    if only_python:
        messages.append(
            f"Очереди есть в TASK_LANES, но нет в CHECK-констрейнте БД: {sorted(only_python)}. "
            "Вставка задачи упадёт в рантайме."
        )
    if only_db:
        messages.append(
            f"Очереди есть в CHECK-констрейнте БД, но нет в TASK_LANES: {sorted(only_db)}. "
            "Задачи в эти очереди никто не заберёт."
        )

    assert not messages, "\n".join(messages)


# ── Параметрические тесты гарда на синтетических образцах ─────────────────────

# SQL-шаблон, воспроизводящий реальный синтаксис PostgreSQL
_SQL_TEMPLATE = "CONSTRAINT ck_task_queue_lane CHECK (((lane)::text = ANY (ARRAY[{values}]))),\n"


def _make_sql_constraint(*lanes: str) -> str:
    values = ", ".join(f"('{lane}'::character varying)::text" for lane in lanes)
    return _SQL_TEMPLATE.format(values=values)


_REAL_LANES = list(TASK_LANES)  # текущее содержимое TASK_LANES


@pytest.mark.parametrize(
    "sql_text, description",
    [
        pytest.param(
            # Лишняя очередь в БД, которой нет в TASK_LANES
            _make_sql_constraint(*_REAL_LANES, "extra_db_only_lane"),
            "extra queue in DB only",
            id="extra_queue_in_db",
        ),
        pytest.param(
            # Недостающая очередь в БД (одна реальная очередь убрана)
            _make_sql_constraint(*_REAL_LANES[1:]),
            "missing queue in DB vs TASK_LANES",
            id="missing_queue_in_db",
        ),
    ],
)
def test_guard_detects_divergence_on_synthetic_sql(
    sql_text: str,
    description: str,
) -> None:
    """Гард обязан покраснеть на синтетическом SQL с расхождением.

    Этот тест проверяет сам механизм детектирования, а не реальный baseline.
    """
    db_lanes = _parse_lanes_from_sql_text(sql_text)

    python_effective = TASK_LANES - ONLY_IN_PYTHON
    db_effective = db_lanes - ONLY_IN_DB

    only_python = python_effective - db_effective
    only_db = db_effective - python_effective

    has_divergence = bool(only_python or only_db)
    assert has_divergence, (
        f"Гард не поймал расхождение для случая {description!r}. "
        f"TASK_LANES={sorted(TASK_LANES)}, db_lanes={sorted(db_lanes)}"
    )
