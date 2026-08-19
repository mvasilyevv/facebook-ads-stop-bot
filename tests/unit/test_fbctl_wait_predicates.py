# -*- coding: utf-8 -*-
"""Ожидание опрашивает состояние, а не чинит его.

19.08.2026 деплой не мог пройти шаг канала стола. Внутрь wait_for был передан
ensure_browser_channel — ручка, которая перезапускает профиль Vision. Опрос раз
в пять секунд давал 36 принудительных перезапусков за 180 секунд, и холодный
старт, идущий дольше интервала опроса, не завершался ни при каком пределе
времени. Лечение — действие, его выполняют один раз; ожидание после этого
только читает.

Гард держит инвариант с двух сторон, потому что одного имени мало:

1. Предикат wait_for обязан называться require_* или _check_*. Имя ищется и в
   позиционном аргументе, и в `check=`, и внутри лямбды целиком — сравнение
   (`require_x(p) == "ok"`), связка (`require_a(p) and require_b(p)`) и
   `partial(require_x, p)` разбираются наравне с голым вызовом.
2. Каждая функция require_*/_check_* в fbctl обязана этому имени
   соответствовать: внутри неё нет ни `runner.run`, ни ручек ensure_/start_/
   restart_/stop_/apply_. Без второй половины гард проверял бы букву имени, а
   переименование ensure_browser_channel в require_browser_channel пропустило
   бы ровно тот дефект, ради которого гард написан.

Предикат, который не удалось разобрать статически, — это провал гарда, а не
повод промолчать: молчаливый пропуск и есть тот способ, которым лечащая ручка
однажды уже проехала в цикл ожидания.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FBCTL = ROOT / "fbctl"

# Соглашение об именах: так называется наблюдение.
_READ_ONLY_PREFIXES = ("require_", "_check_")

# Так называется действие. Имя из этого набора внутри предиката — ровно тот
# дефект, который 19.08.2026 сделал деплой невозможным в принципе.
_HEALING_PREFIXES = ("ensure_", "start_", "restart_", "stop_", "apply_")

# Поведение, а не имя: наблюдение ходит в сеть через status/json, а меняющий
# состояние запрос — через post_json/patch_json. Именно этим ensure-ручка
# перезапускает профиль Vision, и одного переименования её в require_* не хватит,
# чтобы протащить дефект мимо гарда.
_MUTATING_CALLS = frozenset({"post_json", "patch_json"})

# Сообщение об ошибке читает человек, поэтому в него идёт исходник узла, а не
# его синтаксическое дерево.
_MAX_SNIPPET = 80


def _callee_name(node: ast.expr) -> str:
    """Имя вызываемого: `probes.wait_for` и `wait_for` — одно и то же имя."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _snippet(node: ast.AST) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= _MAX_SNIPPET else text[: _MAX_SNIPPET - 1] + "…"


def _value_calls(node: ast.AST) -> list[ast.Call]:
    """Вызовы, чей результат образует значение выражения.

    Обходится всё выражение, а не только корневой узел, поэтому `Compare` и
    `BoolOp` резолвятся так же, как голый вызов. Внутрь аргументов уже
    начатого вызова спуска нет: там считаются аргументы (`_api_origin(config)`),
    а не наблюдается состояние — требовать от них имени предиката значило бы
    краснеть на законном коде. Лечение внутри аргументов ловит `_healing_calls`,
    который обходит выражение целиком.
    """
    found: list[ast.Call] = []
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call):
            found.append(current)
            continue
        stack.extend(ast.iter_child_nodes(current))
    return found


def _is_runner_run(call: ast.Call) -> bool:
    """`runner.run(...)` — исполнение команды на хосте, а не наблюдение."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "run"
        and "runner" in ast.unparse(func.value)
    )


def _healing_calls(nodes: list[ast.AST]) -> list[str]:
    """Имена меняющих состояние вызовов где угодно внутри узлов."""
    offenders: list[str] = []
    for root in nodes:
        for call in ast.walk(root):
            if not isinstance(call, ast.Call):
                continue
            name = _callee_name(call.func)
            if name.startswith(_HEALING_PREFIXES) or name in _MUTATING_CALLS:
                offenders.append(name)
            elif _is_runner_run(call):
                offenders.append(ast.unparse(call.func))
    return list(dict.fromkeys(offenders))


@dataclass(frozen=True)
class _Predicate:
    """Разобранный аргумент `check` одного вызова wait_for."""

    where: str
    observed: tuple[str, ...]
    healing: tuple[str, ...]
    snippet: str


def _predicate_argument(call: ast.Call) -> ast.expr | None:
    """Предикат берётся из второго позиционного аргумента или из `check=`."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "check":
            return keyword.value
    return None


def _resolve(predicate: ast.expr) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(имена наблюдающих вызовов, имена лечащих вызовов) внутри предиката.

    Пустой первый элемент означает «разобрать не удалось» — об этом гард
    сообщает отдельно и явно.
    """
    healing = tuple(_healing_calls([predicate]))
    if isinstance(predicate, ast.Lambda):
        names = tuple(
            name for call in _value_calls(predicate.body) if (name := _callee_name(call.func))
        )
        return names, healing
    if isinstance(predicate, ast.Name | ast.Attribute):
        name = _callee_name(predicate)
        return ((name,) if name else ()), healing
    if isinstance(predicate, ast.Call) and _callee_name(predicate.func) == "partial":
        # partial(require_x, p) — тот же предикат с заранее подставленными
        # аргументами, а не новое поведение.
        if predicate.args and (name := _callee_name(predicate.args[0])):
            return (name,), healing
    return (), healing


def _wait_predicates() -> list[_Predicate]:
    found: list[_Predicate] = []
    for source_file in sorted(_FBCTL.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee_name(node.func) != "wait_for":
                continue
            where = f"{source_file.name}:{node.lineno}"
            predicate = _predicate_argument(node)
            if predicate is None:
                found.append(_Predicate(where, (), (), _snippet(node)))
                continue
            observed, healing = _resolve(predicate)
            found.append(_Predicate(where, observed, healing, _snippet(predicate)))
    return found


def _read_only_functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for source_file in sorted(_FBCTL.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith(_READ_ONLY_PREFIXES):
                found.append((f"{source_file.name}:{node.lineno}:{node.name}", node))
    return found


def test_every_wait_predicate_resolves_statically() -> None:
    predicates = _wait_predicates()
    assert predicates, "не найдено ни одного вызова wait_for — гард смотрит не туда"

    unresolved = [f"{item.where} → {item.snippet}" for item in predicates if not item.observed]
    assert unresolved == [], (
        "предикат ожидания не удалось разобрать: "
        + "; ".join(unresolved)
        + " — гард обязан сказать об этом вслух: молчаливый пропуск и есть тот "
        "способ, которым лечащая ручка однажды уже проехала в цикл ожидания"
    )


def test_every_wait_predicate_is_read_only() -> None:
    predicates = _wait_predicates()
    assert predicates, "не найдено ни одного вызова wait_for — гард смотрит не туда"

    offenders: list[str] = []
    for item in predicates:
        bad = dict.fromkeys(
            [name for name in item.observed if not name.startswith(_READ_ONLY_PREFIXES)]
            + list(item.healing)
        )
        offenders += [f"{item.where} → {name}" for name in bad]
    assert offenders == [], (
        "предикат ожидания обязан только читать (require_* или _check_*): "
        + ", ".join(offenders)
        + " — лечащая ручка на месте пробы повторяет лечение каждый интервал опроса"
    )


def test_read_only_named_functions_really_only_read() -> None:
    """Имя require_*/_check_* — проверяемое утверждение, а не обещание.

    Иначе гард ловил бы букву имени: переименование ensure_browser_channel в
    require_browser_channel прошло бы мимо него вместе с дефектом.
    """
    functions = _read_only_functions()
    assert functions, "в fbctl нет ни одной функции require_*/_check_* — гард смотрит не туда"

    offenders = [
        f"{where} → {name}" for where, node in functions for name in _healing_calls(list(node.body))
    ]
    assert offenders == [], (
        "функция с именем наблюдения выполняет действие: "
        + ", ".join(offenders)
        + " — такое имя разрешено передавать в wait_for, и действие повторится "
        "каждый интервал опроса"
    )
