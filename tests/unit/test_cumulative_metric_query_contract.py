"""Architecture guard for cumulative Meta metric aggregation."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from core.dashboard.metric_aggregation import (
    latest_per_ad_per_day_cte,
    latest_per_ad_window_cte,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (ROOT / "core", ROOT / "apps", ROOT / "packages")
SOURCE_SUFFIXES = frozenset({".py", ".sql", ".ts", ".tsx", ".js", ".mjs"})
IGNORED_PARTS = frozenset({"node_modules", "dist", "build", "coverage"})
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join)\s+(?:(?:public)\s*\.\s*)?ad_metrics\b",
    re.IGNORECASE,
)
_SUM_RE = re.compile(r"\bsum\s*\(", re.IGNORECASE)
_LATEST_BUILDERS: dict[str, Callable[..., str]] = {
    "latest_per_ad_window_cte": latest_per_ad_window_cte,
    "latest_per_ad_per_day_cte": latest_per_ad_per_day_cte,
}


def _normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _combine(parts: Sequence[set[str]]) -> set[str]:
    values = {""}
    for options in parts:
        effective = options or {" __dynamic__ "}
        values = {prefix + suffix for prefix in values for suffix in effective}
    return values


def _resolved_strings(node: ast.expr | None, env: dict[str, set[str]]) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return env.get(node.id, set())
    if isinstance(node, ast.Attribute):
        return env.get(node.attr, set())
    if isinstance(node, ast.JoinedStr):
        parts: list[set[str]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append({value.value})
            elif isinstance(value, ast.FormattedValue):
                parts.append(_resolved_strings(value.value, env))
        return _combine(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _combine([_resolved_strings(node.left, env), _resolved_strings(node.right, env)])
    if isinstance(node, ast.IfExp):
        return _resolved_strings(node.body, env) | _resolved_strings(node.orelse, env)
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name == "text" and node.args:
            return _resolved_strings(node.args[0], env)
        if builder := _LATEST_BUILDERS.get(name or ""):
            alias = "latest_per_ad"
            for keyword in node.keywords:
                if keyword.arg == "cte_alias":
                    aliases = _resolved_strings(keyword.value, env)
                    if len(aliases) == 1:
                        alias = next(iter(aliases))
            return {builder(cte_alias=alias)}
    return set()


def _capture_text_queries(node: ast.AST | None, env: dict[str, set[str]]) -> set[str]:
    if node is None:
        return set()
    queries: set[str] = set()
    for candidate in ast.walk(node):
        if (
            isinstance(candidate, ast.Call)
            and _call_name(candidate.func) == "text"
            and candidate.args
        ):
            queries.update(_resolved_strings(candidate.args[0], env))
    return queries


def _assigned_names(target: ast.expr) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            yield from _assigned_names(item)


def _scan_statements(
    statements: Sequence[ast.stmt],
    env: dict[str, set[str]],
    queries: set[str],
) -> None:
    for statement in statements:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            queries.update(_capture_text_queries(value, env))
            resolved = _resolved_strings(value, env)
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if resolved:
                for target in targets:
                    for name in _assigned_names(target):
                        env[name] = resolved
            continue
        if isinstance(statement, ast.If):
            queries.update(_capture_text_queries(statement.test, env))
            left = env.copy()
            right = env.copy()
            _scan_statements(statement.body, left, queries)
            _scan_statements(statement.orelse, right, queries)
            for name in left.keys() | right.keys():
                merged = left.get(name, set()) | right.get(name, set())
                if merged:
                    env[name] = merged
            continue
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            for item in statement.items:
                queries.update(_capture_text_queries(item.context_expr, env))
            _scan_statements(statement.body, env, queries)
            continue
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            expression = statement.test if isinstance(statement, ast.While) else statement.iter
            queries.update(_capture_text_queries(expression, env))
            _scan_statements(statement.body, env, queries)
            _scan_statements(statement.orelse, env, queries)
            continue
        if isinstance(statement, (ast.Try, ast.TryStar)):
            _scan_statements(statement.body, env.copy(), queries)
            for handler in statement.handlers:
                _scan_statements(handler.body, env.copy(), queries)
            _scan_statements(statement.orelse, env.copy(), queries)
            _scan_statements(statement.finalbody, env.copy(), queries)
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        queries.update(_capture_text_queries(statement, env))


def _scan_scope(
    statements: Sequence[ast.stmt],
    inherited_env: dict[str, set[str]],
    queries: set[str],
) -> None:
    scope_env = inherited_env.copy()
    _scan_statements(statements, scope_env, queries)
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _scan_scope(statement.body, scope_env, queries)


def _python_composed_sql_queries(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    queries: set[str] = set()
    _scan_scope(tree.body, {}, queries)
    return queries


def _python_string_templates(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    joined_parts = {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in joined_parts
        ):
            yield node.value


def _source_templates(path: Path) -> Iterator[str]:
    if path.suffix == ".py":
        yield from _python_string_templates(path)
        yield from _python_composed_sql_queries(path)
        return
    yield path.read_text(encoding="utf-8")


def _paren_depths(sql: str) -> list[int]:
    depths: list[int] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        depths.append(depth)
        if quote is not None:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    depths.append(depth)
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        index += 1
    return depths


def _matching_paren(sql: str, opening: int) -> int | None:
    depths = _paren_depths(sql)
    opening_depth = depths[opening]
    for index in range(opening + 1, len(sql)):
        if sql[index] == ")" and depths[index] == opening_depth + 1:
            return index
    return None


def _cte_body(sql: str, name: str, before: int) -> str | None:
    pattern = re.compile(rf"\b{re.escape(name)}\s+as\s*\(", re.IGNORECASE)
    matches = [match for match in pattern.finditer(sql, 0, before)]
    if not matches:
        return None
    opening = sql.find("(", matches[-1].start())
    closing = _matching_paren(sql, opening)
    return sql[opening + 1 : closing] if closing is not None else None


def _uses_correlated_latest_max(sql: str, *, required_alias: str | None = None) -> bool:
    outer_relation = re.compile(
        r"\b(?:from|join)\s+(?:(?:public)\s*\.\s*)?ad_metrics\s+"
        r"(?:as\s+)?([a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    )
    for relation in outer_relation.finditer(sql):
        relation_alias = relation.group(1).lower()
        if required_alias is not None and relation_alias != required_alias:
            continue
        outer_alias = re.escape(relation_alias)
        latest_for_same_ad = re.compile(
            rf"\b{outer_alias}\.cycle_ts\s*=\s*\(\s*select\s+max\s*\(\s*"
            rf"(?P<inner>[a-z_][a-z0-9_]*)\.cycle_ts\s*\)\s+from\s+"
            rf"(?:(?:public)\s*\.\s*)?ad_metrics\s+(?:as\s+)?(?P=inner)\s+"
            rf"where\b.*?(?P=inner)\.ad_id\s*=\s*{outer_alias}\.ad_id\b",
            re.IGNORECASE,
        )
        if latest_for_same_ad.search(sql):
            return True
    return False


def _selects_latest_per_ad(sql: str, *, required_alias: str | None = None) -> bool:
    normalized = _normalized_sql(sql)
    if _TABLE_REF_RE.search(normalized) is None:
        return False
    if _uses_correlated_latest_max(normalized, required_alias=required_alias):
        return True
    distinct_at = normalized.find("distinct on")
    table_at = normalized.find("ad_metrics")
    order_at = normalized.find("order by", table_at)
    if distinct_at >= 0 and distinct_at < table_at and "ad_id" in normalized[distinct_at:table_at]:
        return order_at >= 0 and "cycle_ts desc" in normalized[order_at:]
    return (
        "lateral" in normalized
        and order_at >= 0
        and "cycle_ts desc" in normalized[order_at:]
        and re.search(r"\blimit\s+1\b", normalized[order_at:]) is not None
    )


def _select_block(sql: str, position: int, depth: int) -> str:
    depths = _paren_depths(sql)
    select_starts = [
        match.start()
        for match in re.finditer(r"\bselect\b", sql, re.IGNORECASE)
        if match.start() <= position and depths[match.start()] == depth
    ]
    start = select_starts[-1] if select_starts else 0
    end = len(sql)
    for index in range(position + 1, len(sql)):
        if depths[index] < depth:
            end = index
            break
    return sql[start:end]


def _same_depth_relations(sql: str, start: int, depth: int) -> list[str]:
    depths = _paren_depths(sql)
    relations: list[str] = []
    pattern = re.compile(
        r"\b(?:from|join)\s+(?:(?:public)\s*\.\s*)?([a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql, start):
        if depths[match.start()] < depth:
            break
        if depths[match.start()] == depth:
            relations.append(match.group(1).lower())
    return relations


def _lateral_body_for_alias(sql: str, alias: str, start: int) -> str | None:
    pattern = re.compile(r"\bjoin\s+lateral\s*\(", re.IGNORECASE)
    for match in pattern.finditer(sql, start):
        opening = sql.find("(", match.start())
        closing = _matching_paren(sql, opening)
        if closing is None:
            continue
        tail = sql[closing + 1 : closing + 80]
        alias_match = re.match(
            r"\s*(?:as\s+)?([a-z_][a-z0-9_]*)",
            tail,
            re.IGNORECASE,
        )
        if alias_match and alias_match.group(1).lower() == alias:
            return sql[opening + 1 : closing]
    return None


def _sum_reads_raw_snapshots(sql: str, sum_match: re.Match[str]) -> bool:
    depths = _paren_depths(sql)
    depth = depths[sum_match.start()]
    relations = _same_depth_relations(sql, sum_match.end(), depth)
    argument_end = _matching_paren(sql, sql.find("(", sum_match.start()))
    qualifier: str | None = None
    if argument_end is not None:
        argument = sql[sum_match.end() : argument_end]
        qualifier_match = re.search(r"\b([a-z_][a-z0-9_]*)\s*\.", argument, re.IGNORECASE)
        if qualifier_match:
            qualifier = qualifier_match.group(1).lower()
    if "ad_metrics" in relations:
        return not _selects_latest_per_ad(
            _select_block(sql, sum_match.start(), depth),
            required_alias=qualifier,
        )
    if not relations:
        return False

    primary = relations[0]
    body = _cte_body(sql, primary, sum_match.start())
    if body is not None and _TABLE_REF_RE.search(body):
        return not _selects_latest_per_ad(body)

    if qualifier is not None:
        lateral = _lateral_body_for_alias(sql, qualifier, sum_match.end())
        if lateral is not None and _TABLE_REF_RE.search(lateral):
            return not _selects_latest_per_ad("LATERAL " + lateral)
    return False


def _aggregates_raw_ad_metrics(sql: str) -> bool:
    normalized = _normalized_sql(sql)
    if _TABLE_REF_RE.search(normalized) is None:
        return False
    return any(
        _sum_reads_raw_snapshots(normalized, match) for match in _SUM_RE.finditer(normalized)
    )


@pytest.mark.parametrize(
    ("sql", "unsafe"),
    [
        ("SELECT SUM(spend) FROM ad_metrics", True),
        ("SELECT SUM(metric.spend) FROM public.ad_metrics AS metric", True),
        ("SELECT SUM(metric.spend) FROM offers JOIN ad_metrics AS metric ON TRUE", True),
        (
            """
            WITH latest AS (
                SELECT DISTINCT ON (ad_id) ad_id, spend
                FROM ad_metrics
                ORDER BY ad_id, cycle_ts DESC
            )
            SELECT SUM(spend) FROM latest
            """,
            False,
        ),
        (
            """
            WITH unrelated_latest AS (
                SELECT DISTINCT ON (ad_id) ad_id, spend
                FROM ad_metrics
                ORDER BY ad_id, cycle_ts DESC
            )
            SELECT SUM(raw.spend) FROM ad_metrics AS raw
            """,
            True,
        ),
        (
            """
            SELECT SUM(latest.spend)
            FROM fb_ads AS ad
            LEFT JOIN LATERAL (
                SELECT metric.spend
                FROM ad_metrics AS metric
                WHERE metric.ad_id = ad.id
                ORDER BY metric.cycle_ts DESC
                LIMIT 1
            ) AS latest ON TRUE
            """,
            False,
        ),
        (
            """
            SELECT SUM(metric.spend)
            FROM ad_metrics AS metric
            WHERE metric.cycle_ts = (
                SELECT MAX(newer.cycle_ts)
                FROM ad_metrics AS newer
                WHERE newer.ad_id = metric.ad_id
            )
            """,
            False,
        ),
        (
            """
            SELECT SUM(raw.spend)
            FROM ad_metrics AS raw
            JOIN ad_metrics AS latest ON latest.ad_id = raw.ad_id
            WHERE latest.cycle_ts = (
                SELECT MAX(newer.cycle_ts)
                FROM ad_metrics AS newer
                WHERE newer.ad_id = latest.ad_id
            )
            """,
            True,
        ),
    ],
)
def test_cumulative_metric_guard_distinguishes_raw_sum_from_latest_per_ad(
    sql: str,
    unsafe: bool,
) -> None:
    assert _aggregates_raw_ad_metrics(sql) is unsafe


@pytest.mark.parametrize(
    "scope",
    [
        """
def unsafe_query():
    raw = "raw AS (SELECT spend FROM public.ad_metrics)"
    return text(f"WITH {raw} SELECT {_SUM} FROM raw")
""",
        """
class QueryRepository:
    _SUM = "SUM(spend)"

    def unsafe_query(self):
        raw = "raw AS (SELECT spend FROM public.ad_metrics)"
        return text(f"WITH {raw} SELECT {self._SUM} FROM raw")
""",
    ],
    ids=["function", "class-method"],
)
def test_source_guard_reconstructs_split_fstrings_in_every_scope(
    tmp_path: Path,
    scope: str,
) -> None:
    source = tmp_path / "composed_query.py"
    source.write_text(
        f"""
from sqlalchemy import text

_SUM = "SUM(spend)"

{scope}
""",
        encoding="utf-8",
    )

    queries = _python_composed_sql_queries(source)

    assert any(_aggregates_raw_ad_metrics(query) for query in queries)


@pytest.mark.parametrize("builder", _LATEST_BUILDERS.values(), ids=_LATEST_BUILDERS)
def test_cumulative_metric_builders_select_latest_before_sum(
    builder: Callable[..., str],
) -> None:
    cte = builder(cte_alias="latest")

    assert not _aggregates_raw_ad_metrics(f"WITH {cte} SELECT SUM(spend) FROM latest")


def test_backend_and_shared_packages_never_sum_raw_cumulative_snapshots() -> None:
    violations: list[str] = []
    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in SOURCE_SUFFIXES
                or IGNORED_PARTS.intersection(path.parts)
            ):
                continue
            for template in _source_templates(path):
                if _aggregates_raw_ad_metrics(template):
                    violations.append(str(path.relative_to(ROOT)))
                    break

    # Raw cumulative SUM inflates spend and can trigger a premature money stop.
    assert violations == []
