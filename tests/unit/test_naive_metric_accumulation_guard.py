"""Architecture guard: кумулятивные поля ad_metrics не суммируются наивно.

Покрывает два рантайма:
- Python: SQLAlchemy ORM func.sum(Model.field) на кумулятивных полях ad_metrics.
- TypeScript/JS: накопление через + или += по снапшотам метрик.

SQL-строчный SUM(...) уже покрыт test_cumulative_metric_query_contract.py.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARTS = frozenset({"node_modules", "dist", "build", "coverage", "__pycache__"})

# Кумулятивные счётчики (core/models/observer/ad_metrics.py, 9 полей).
# Растут в течение cabinet-day и сбрасываются при reset'е суток.
# Наивный SUM по снапшотам завышает значение в N раз (CRIT-1).
CUMULATIVE_FIELDS: frozenset[str] = frozenset(
    {
        "spend",
        "reach",
        "impressions",
        "clicks",
        "leads",
        "registrations",
        "deposits",
        "outbound_clicks",
        "landing_page_views",
    }
)

# Производные отношения (9 полей) — суммировать бессмысленно, но их тоже
# не должно быть в func.sum(Model.field). В SQL уже покрыты строчным гардом.
_DERIVED_METRIC_FIELDS: frozenset[str] = frozenset(
    {
        "cpc",
        "ctr",
        "cost_per_result",
        "cpm",
        "frequency",
        "cost_per_lead",
        "cost_per_registration",
        "outbound_ctr",
        "cost_per_landing_page_view",
    }
)

# Полный набор полей для Python-гарда (ORM нельзя sum ни одно из 18).
_ALL_METRIC_FIELDS: frozenset[str] = CUMULATIVE_FIELDS | _DERIVED_METRIC_FIELDS

# ---------------------------------------------------------------------------
# Разрешённые списки — пустые: легитимных использований нет.
# Любая запись требует явного комментария-обоснования.
# ---------------------------------------------------------------------------

# Python-файлы (relative to ROOT), где func.sum на поле ad_metrics разрешён.
# Пусто: в кодовой базе нет корректного использования func.sum по снапшотам;
# корректная агрегация идёт через SQL-CTE в metric_aggregation.py.
_PY_FUNC_SUM_ALLOWED: frozenset[str] = frozenset()

# TypeScript/JS-файлы (relative to ROOT), где накопление поля разрешено.
# Пусто: фронт и browser-agent получают уже агрегированные данные от сервера
# и не должны складывать сырые снапшоты на клиентской стороне.
_TS_ACCUM_ALLOWED: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# Директории для сканирования
# ---------------------------------------------------------------------------

_PY_ROOTS = (ROOT / "core", ROOT / "apps")
_TS_ROOTS = (
    ROOT / "packages",
    ROOT / "frontend",
    ROOT / "frontend-mini",
    ROOT / "services" / "browser-agent",
)
_TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".mjs"})


# ---------------------------------------------------------------------------
# Python: обнаружение func.sum(Model.field) через AST
# ---------------------------------------------------------------------------


def _is_func_sum_call(node: ast.expr) -> bool:
    """True если узел — вызов *.sum(...): func.sum, sa.func.sum, sqlalchemy.func.sum."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "sum"


def _arg_is_metric_field(arg: ast.expr) -> bool:
    """True если аргумент — Model.field, где field ∈ _ALL_METRIC_FIELDS."""
    return isinstance(arg, ast.Attribute) and arg.attr in _ALL_METRIC_FIELDS


def _func_sum_on_metric_field(source: str) -> bool:
    """True если в Python-коде есть func.sum(Model.cumulative_field)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not _is_func_sum_call(node):
            continue
        for arg in node.args:  # type: ignore[attr-defined]
            if _arg_is_metric_field(arg):
                return True
    return False


# ---------------------------------------------------------------------------
# TypeScript: обнаружение + / += по кумулятивным полям
# ---------------------------------------------------------------------------

# Ловит: `acc + row.spend`, `total += m.impressions`, `sum += a.clicks` и т.п.
# Требует `.field` (точка + имя поля), что исключает простые `count += 1`.
_TS_NAIVE_ACCUM_RE = re.compile(
    r"(?:\+=|\+)\s*\w[\w.]*\.(?:" + "|".join(sorted(CUMULATIVE_FIELDS)) + r")\b",
)


def _ts_accumulates_cumulative_field(source: str) -> bool:
    """True если TypeScript/JS содержит наивное сложение кумулятивного поля."""
    return bool(_TS_NAIVE_ACCUM_RE.search(source))


# ---------------------------------------------------------------------------
# Воспроизведённые образцы нарушений (фикстуры гарда)
# ---------------------------------------------------------------------------

_PYTHON_VIOLATION_SAMPLE = textwrap.dedent("""\
    from sqlalchemy import func
    from core.models.observer.ad_metrics import AdMetrics

    def total_spend(session):
        # Нарушение: func.sum по кумулятивному снапшоту без latest-per-ad
        return session.query(func.sum(AdMetrics.spend)).scalar()
""")

_TS_VIOLATION_SAMPLE = textwrap.dedent("""\
    // Нарушение: наивный reduce по массиву снапшотов ad_metrics
    function totalSpend(snapshots: Array<{ spend: number }>): number {
        return snapshots.reduce((acc, row) => acc + row.spend, 0);
    }
""")


# ---------------------------------------------------------------------------
# Тесты: гард ловит образцы нарушений
# ---------------------------------------------------------------------------


def test_python_sample_func_sum_is_detected() -> None:
    """func.sum(AdMetrics.spend) без latest-per-ad обязан быть пойман гардом."""
    assert _func_sum_on_metric_field(_PYTHON_VIOLATION_SAMPLE), (
        "Гард не поймал func.sum на кумулятивном поле AdMetrics"
    )


def test_typescript_sample_reduce_is_detected() -> None:
    """reduce/+ по spend в TypeScript обязан быть пойман гардом."""
    assert _ts_accumulates_cumulative_field(_TS_VIOLATION_SAMPLE), (
        "Гард не поймал наивное накопление cumulative поля в TypeScript"
    )


# ---------------------------------------------------------------------------
# Тесты: весь репозиторий проходит гард
# ---------------------------------------------------------------------------


def test_no_func_sum_on_cumulative_ad_metric_fields() -> None:
    """Ни один Python-файл (кроме разрешённых) не применяет func.sum к полям ad_metrics."""
    violations: list[str] = []
    for root in _PY_ROOTS:
        if not root.exists():
            raise FileNotFoundError(f"Директория не найдена: {root}")
        for path in root.rglob("*.py"):
            if IGNORED_PARTS.intersection(path.parts):
                continue
            rel = str(path.relative_to(ROOT))
            if rel in _PY_FUNC_SUM_ALLOWED:
                continue
            if _func_sum_on_metric_field(path.read_text(encoding="utf-8")):
                violations.append(rel)

    assert violations == [], (
        "func.sum на полях ad_metrics без latest-per-ad обнаружен в:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nЕсли latest-per-ad применён явно — добавь файл в _PY_FUNC_SUM_ALLOWED с обоснованием."
    )


def test_no_ts_accumulation_of_cumulative_metric_fields() -> None:
    """Ни один TypeScript/JS файл не суммирует кумулятивные поля ad_metrics через + или +=."""
    violations: list[str] = []
    for root in _TS_ROOTS:
        if not root.exists():
            raise FileNotFoundError(f"Директория не найдена: {root}")
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in _TS_SUFFIXES
                or IGNORED_PARTS.intersection(path.parts)
            ):
                continue
            rel = str(path.relative_to(ROOT))
            if rel in _TS_ACCUM_ALLOWED:
                continue
            if _ts_accumulates_cumulative_field(path.read_text(encoding="utf-8")):
                violations.append(rel)

    assert violations == [], (
        "Наивное накопление кумулятивного поля ad_metrics обнаружено в:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nЕсли поле берётся из агрегированного источника — добавь файл в _TS_ACCUM_ALLOWED с обоснованием."
    )
