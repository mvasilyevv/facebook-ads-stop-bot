# -*- coding: utf-8 -*-
"""Гард синхронизации набора значений severity.

Три объявления severity-набора обязаны содержать одинаковый набор значений:

  - apps/api/routers/v1/schemas/operator.py :: OperatorSeverity(StrEnum)
      Источник OpenAPI; значения экспортируются в TypeScript через генерацию.
  - core/adoption/bundle.py :: Severity = Literal[...]
      Контракт adoption-bundle/v1; используется в min_severity recipient preference.
  - core/telegram/schemas.py :: NotificationSeverity = Literal[...]
      Контракт outbox-уведомлений; используется в NotificationEventSpec.severity.

Сегодня все три содержат {"ok", "warning", "critical", "unknown"}.
Если один набор расходится — оператор получает рассинхрон: Telegram говорит
одно, интерфейс показывает другое. Гард ловит расхождение до попадания в CI.

DataState и OperatorActionState объявлены только в operator.py; независимых
двойников не обнаружено — они вне этого гарда.

Явные исключения
----------------
Ни одного: на момент написания все три объявления синхронны. PreferenceThreshold
намеренно шире (добавляет "off" и "inherit") и не является базовым набором severity;
включать его в гард нельзя — это отдельный контракт.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Пути к источникам относительно корня репозитория
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent
_OPERATOR_SCHEMA = _REPO_ROOT / "apps" / "api" / "routers" / "v1" / "schemas" / "operator.py"
_BUNDLE = _REPO_ROOT / "core" / "adoption" / "bundle.py"
_TELEGRAM_SCHEMAS = _REPO_ROOT / "core" / "telegram" / "schemas.py"

_CANONICAL_SEVERITY = frozenset({"ok", "warning", "critical", "unknown"})


# ---------------------------------------------------------------------------
# Парсеры
# ---------------------------------------------------------------------------


def _parse_strenum_values(source: str, class_name: str) -> frozenset[str]:
    """Извлечь значения из StrEnum-класса методом AST.

    Поддерживается форма:
        class Foo(StrEnum):
            BAR = "bar"
            BAZ = "baz"
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        values: set[str] = set()
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                values.add(stmt.value.value)
        return frozenset(values)
    raise AssertionError(f"StrEnum class {class_name!r} not found in parsed source")


def _parse_literal_alias_values(source: str, alias_name: str) -> frozenset[str]:
    """Извлечь значения из Literal-алиаса методом регулярного выражения.

    Поддерживается форма (без переносов строк внутри):
        Foo = Literal["a", "b", "c"]
    """
    pattern = rf"^{re.escape(alias_name)}\s*=\s*Literal\[([^\]]+)\]"
    m = re.search(pattern, source, re.MULTILINE)
    if not m:
        raise AssertionError(f"Literal alias {alias_name!r} not found in source")
    # Извлечь строковые значения из содержимого скобок: "ok", "warning", ...
    raw = m.group(1)
    values: set[str] = set()
    for token in re.findall(r'"([^"]+)"|\'([^\']+)\'', raw):
        values.add(token[0] or token[1])
    return frozenset(values)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_operator_severity_values() -> None:
    """OperatorSeverity содержит ожидаемый canonical-набор."""
    source = _OPERATOR_SCHEMA.read_text(encoding="utf-8")
    values = _parse_strenum_values(source, "OperatorSeverity")
    assert values == _CANONICAL_SEVERITY, (
        f"OperatorSeverity values changed: {sorted(values)!r}. "
        "Синхронно обновите bundle.py::Severity и telegram/schemas.py::NotificationSeverity."
    )


def test_bundle_severity_values() -> None:
    """bundle.py::Severity содержит ожидаемый canonical-набор."""
    source = _BUNDLE.read_text(encoding="utf-8")
    values = _parse_literal_alias_values(source, "Severity")
    assert values == _CANONICAL_SEVERITY, (
        f"bundle.py Severity values changed: {sorted(values)!r}. "
        "Синхронно обновите OperatorSeverity и NotificationSeverity."
    )


def test_notification_severity_values() -> None:
    """NotificationSeverity содержит ожидаемый canonical-набор."""
    source = _TELEGRAM_SCHEMAS.read_text(encoding="utf-8")
    values = _parse_literal_alias_values(source, "NotificationSeverity")
    assert values == _CANONICAL_SEVERITY, (
        f"NotificationSeverity values changed: {sorted(values)!r}. "
        "Синхронно обновите OperatorSeverity и bundle.py::Severity."
    )


def test_all_three_severity_sets_are_equal() -> None:
    """Все три объявления severity содержат один и тот же набор значений.

    Это главный гард: изменение набора в одном месте без синхронного
    изменения в остальных провалит именно этот тест.
    """
    op_source = _OPERATOR_SCHEMA.read_text(encoding="utf-8")
    operator_values = _parse_strenum_values(op_source, "OperatorSeverity")

    bundle_source = _BUNDLE.read_text(encoding="utf-8")
    bundle_values = _parse_literal_alias_values(bundle_source, "Severity")

    tg_source = _TELEGRAM_SCHEMAS.read_text(encoding="utf-8")
    tg_values = _parse_literal_alias_values(tg_source, "NotificationSeverity")

    assert operator_values == bundle_values == tg_values, (
        "Severity sets are out of sync!\n"
        f"  OperatorSeverity    : {sorted(operator_values)}\n"
        f"  bundle.py Severity  : {sorted(bundle_values)}\n"
        f"  NotificationSeverity: {sorted(tg_values)}\n"
        "Добавление значения в один источник обязано синхронно появиться в остальных двух."
    )


def test_preference_threshold_is_wider_than_severity() -> None:
    """PreferenceThreshold намеренно шире базового severity-набора.

    Содержит "off" и "inherit" помимо четырёх базовых значений.
    Этот тест фиксирует, что PreferenceThreshold нельзя отождествлять
    с базовым severity и схлопывать с ним.
    """
    bundle_source = _BUNDLE.read_text(encoding="utf-8")
    threshold_values = _parse_literal_alias_values(bundle_source, "PreferenceThreshold")
    severity_values = _parse_literal_alias_values(bundle_source, "Severity")

    assert severity_values < threshold_values, (
        f"PreferenceThreshold должен строго содержать все значения Severity. "
        f"Severity: {sorted(severity_values)}, Threshold: {sorted(threshold_values)}"
    )
    extra = threshold_values - severity_values
    assert "off" in extra and "inherit" in extra, (
        f"PreferenceThreshold должен содержать 'off' и 'inherit' помимо базового набора. "
        f"Лишние значения: {sorted(extra)}"
    )
