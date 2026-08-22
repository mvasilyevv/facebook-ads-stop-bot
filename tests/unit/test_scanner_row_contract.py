# -*- coding: utf-8 -*-
"""Гард синхронизации строки сканирования.

Три описания ScannedAdRow обязаны совпадать по набору полей:
  - Python dataclass  : core/scanner/models.py :: ScannedAdRow
  - proto message     : proto/v1/scanner.proto :: ScannedAdRow
  - TS toProtoRow     : services/browser-agent/src/index.ts :: toProtoRow()

Гард парсит источники статически — поднимать gRPC не требуется.
Расхождение валит тест до CI браузерного слоя.

Явные исключения
----------------
Ни одного: на момент написания все три описания синхронны.
Каждая будущая запись обязана объяснять, почему поле намеренно
присутствует только на одной стороне.

EXCLUSIONS = {
    # Пример:
    # "some_field": "только в proto; TS-side deprecated, Python читает из другого источника"
}
"""

from __future__ import annotations

import dataclasses
import re
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Явные исключения: поле → объяснение причины
# ---------------------------------------------------------------------------
EXCLUSIONS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Пути к источникам относительно корня репозитория
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent
_PROTO_PATH = _REPO_ROOT / "proto" / "v1" / "scanner.proto"
_INDEX_TS_PATH = _REPO_ROOT / "services" / "browser-agent" / "src" / "index.ts"


# ---------------------------------------------------------------------------
# Парсеры
# ---------------------------------------------------------------------------


def _parse_proto_message_fields(proto_text: str, message_name: str) -> set[str]:
    """Извлечь имена полей из named proto message.

    Поддерживает синтаксис:
        <type> <name> = <number>;
        optional <type> <name> = <number>;
    Комментарии // убираются построчно до парсинга.
    """
    # Найти блок message MessageName { … }
    pattern = rf"message\s+{re.escape(message_name)}\s*\{{([^}}]*)\}}"
    m = re.search(pattern, proto_text, re.DOTALL)
    if not m:
        raise ValueError(f"message {message_name!r} не найден в proto:\n{proto_text[:300]}")
    body = m.group(1)

    fields: set[str] = set()
    for raw_line in body.splitlines():
        # Убрать inline-комментарий
        line = re.sub(r"//.*$", "", raw_line).strip()
        if not line:
            continue
        # optional <type> <name> = <n>; или <type> <name> = <n>;
        field_m = re.match(
            r"^(?:optional\s+)?\S+\s+(\w+)\s*=\s*\d+\s*;",
            line,
        )
        if field_m:
            fields.add(field_m.group(1))
    return fields


def _parse_to_proto_row_keys(ts_text: str) -> set[str]:
    """Извлечь ключи из тела функции toProtoRow в index.ts.

    Функция возвращает объект-литерал; берём все ключи вида:
        <ident>: ...
    на верхнем уровне объекта (не вложенных).

    Условный spread `...(cond ? {} : { key: val })` тоже учитывается -
    извлекаем ключи из обеих веток.
    """
    # Найти функцию toProtoRow и взять её тело до парного `}`
    fn_start = ts_text.find("function toProtoRow(")
    if fn_start == -1:
        raise ValueError("функция toProtoRow не найдена в index.ts")

    # Простой счётчик скобок для извлечения тела функции
    depth = 0
    start_body = ts_text.find("{", fn_start)
    if start_body == -1:
        raise ValueError("не найдено открывающее { после toProtoRow")
    i = start_body
    while i < len(ts_text):
        if ts_text[i] == "{":
            depth += 1
        elif ts_text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = ts_text[start_body : i + 1]

    # Убрать строковые литералы и комментарии (грубо), чтобы не ловить ключи в строках
    fn_body_clean = re.sub(r"//[^\n]*", "", fn_body)
    fn_body_clean = re.sub(r"/\*.*?\*/", "", fn_body_clean, flags=re.DOTALL)

    keys: set[str] = set()
    # Прямые ключи объекта верхнего уровня: строки с отступом 4-6 пробелов внутри return { }
    for m in re.finditer(r"^\s{4,6}(\w+)\s*:", fn_body_clean, re.MULTILINE):
        keys.add(m.group(1))
    # Ключи внутри spread тернаров: `{ key: expr }`
    for m in re.finditer(r"\{\s*(\w+)\s*:[^}]+\}", fn_body_clean):
        keys.add(m.group(1))

    # Убрать служебные слова, которые не являются полями строки
    _TS_RESERVED = {"return", "function", "const", "let", "var", "if", "else"}
    return keys - _TS_RESERVED


def _python_dataclass_fields(cls: type) -> set[str]:
    """Имена полей Python dataclass."""
    return {f.name for f in dataclasses.fields(cls)}


# ---------------------------------------------------------------------------
# Вспомогательная функция сравнения (используется и в самопроверочных тестах)
# ---------------------------------------------------------------------------


def _check_sync(
    *,
    python_fields: set[str],
    proto_fields: set[str],
    ts_fields: set[str],
    exclusions: set[str],
) -> list[str]:
    """Вернуть список расхождений (пустой → всё ОК)."""
    problems: list[str] = []

    in_python = python_fields - exclusions
    in_proto = proto_fields - exclusions
    in_ts = ts_fields - exclusions

    # Python <-> proto
    only_python = in_python - in_proto
    only_proto_vs_python = in_proto - in_python
    if only_python:
        problems.append(f"В ScannedAdRow (Python), но нет в proto: {sorted(only_python)}")
    if only_proto_vs_python:
        problems.append(
            f"В proto ScannedAdRow, но нет в Python dataclass: {sorted(only_proto_vs_python)}"
        )

    # proto <-> toProtoRow (TS)
    only_proto_vs_ts = in_proto - in_ts
    only_ts = in_ts - in_proto
    if only_proto_vs_ts:
        problems.append(
            f"В proto ScannedAdRow, но не заполняется в toProtoRow (TS): {sorted(only_proto_vs_ts)}"
        )
    if only_ts:
        problems.append(f"В toProtoRow (TS), но нет в proto ScannedAdRow: {sorted(only_ts)}")

    return problems


# ---------------------------------------------------------------------------
# Основной гард
# ---------------------------------------------------------------------------


def test_scanner_row_contract_three_way_sync() -> None:
    """ScannedAdRow описан одинаково во всех трёх местах.

    Падает, если:
    - поле есть в Python, но не в proto;
    - поле есть в proto, но не читается _proto_to_row (через Python dataclass);
    - поле есть в proto, но не заполняется toProtoRow в index.ts.
    """
    assert _PROTO_PATH.exists(), f"proto не найден: {_PROTO_PATH}"
    assert _INDEX_TS_PATH.exists(), f"index.ts не найден: {_INDEX_TS_PATH}"

    proto_text = _PROTO_PATH.read_text(encoding="utf-8")
    ts_text = _INDEX_TS_PATH.read_text(encoding="utf-8")

    from core.scanner.models import ScannedAdRow

    python_fields = _python_dataclass_fields(ScannedAdRow)
    proto_fields = _parse_proto_message_fields(proto_text, "ScannedAdRow")
    ts_fields = _parse_to_proto_row_keys(ts_text)

    problems = _check_sync(
        python_fields=python_fields,
        proto_fields=proto_fields,
        ts_fields=ts_fields,
        exclusions=set(EXCLUSIONS.keys()),
    )

    assert not problems, "Расхождение ScannedAdRow между Python/proto/toProtoRow:\n" + "\n".join(
        f"  * {p}" for p in problems
    )


# ---------------------------------------------------------------------------
# Самопроверка гарда: образцы расхождений
# ---------------------------------------------------------------------------


_MINIMAL_PROTO_SYNC = textwrap.dedent(
    """\
    syntax = "proto3";
    message ScannedAdRow {
      string fb_ad_id = 1;
      string spend = 2;
      optional string moderation_reason = 3;
    }
    """
)

_MINIMAL_TS_SYNC = textwrap.dedent(
    """\
    function toProtoRow(row: any): any {
      return {
        fb_ad_id: row.fb_ad_id,
        spend: row.spend,
        ...(row.moderation_reason === null ? {} : { moderation_reason: row.moderation_reason }),
      };
    }
    """
)

_MINIMAL_PYTHON_FIELDS = {"fb_ad_id", "spend", "moderation_reason"}


def test_guard_self_check_passes_on_synced_sample() -> None:
    """Гард молчит, когда образцы синхронны."""
    proto_fields = _parse_proto_message_fields(_MINIMAL_PROTO_SYNC, "ScannedAdRow")
    ts_fields = _parse_to_proto_row_keys(_MINIMAL_TS_SYNC)
    problems = _check_sync(
        python_fields=_MINIMAL_PYTHON_FIELDS,
        proto_fields=proto_fields,
        ts_fields=ts_fields,
        exclusions=set(),
    )
    assert not problems, f"Неожиданные расхождения на синхронном образце: {problems}"


def test_guard_catches_field_missing_from_proto() -> None:
    """Поле есть в Python-датаклассе, но отсутствует в proto -> гард краснеет."""
    # proto не содержит `new_field`, а python_fields содержит
    python_fields = _MINIMAL_PYTHON_FIELDS | {"new_field"}
    proto_fields = _parse_proto_message_fields(_MINIMAL_PROTO_SYNC, "ScannedAdRow")
    ts_fields = _parse_to_proto_row_keys(_MINIMAL_TS_SYNC)
    problems = _check_sync(
        python_fields=python_fields,
        proto_fields=proto_fields,
        ts_fields=ts_fields,
        exclusions=set(),
    )
    assert any("new_field" in p for p in problems), (
        f"Гард должен был поймать 'new_field', но проблем нет: {problems}"
    )


def test_guard_catches_field_missing_from_to_proto_row() -> None:
    """Поле есть в proto, но пропущено в toProtoRow -> гард краснеет."""
    # TS-функция не содержит spend
    ts_without_spend = textwrap.dedent(
        """\
        function toProtoRow(row: any): any {
          return {
            fb_ad_id: row.fb_ad_id,
            ...(row.moderation_reason === null ? {} : { moderation_reason: row.moderation_reason }),
          };
        }
        """
    )
    proto_fields = _parse_proto_message_fields(_MINIMAL_PROTO_SYNC, "ScannedAdRow")
    ts_fields = _parse_to_proto_row_keys(ts_without_spend)
    problems = _check_sync(
        python_fields=_MINIMAL_PYTHON_FIELDS,
        proto_fields=proto_fields,
        ts_fields=ts_fields,
        exclusions=set(),
    )
    assert any("spend" in p for p in problems), (
        f"Гард должен был поймать отсутствие 'spend' в toProtoRow, но проблем нет: {problems}"
    )
