# -*- coding: utf-8 -*-
"""Creative Report — замыкает петлю «хук → креатив → депозит».

Джойнит Creative Registry (docs/creatives/*.yaml) с подтверждённой click-state
проекцией трекера (event tags: sub3=creative_code, sub6=угол) и считает:
  - leaderboard креативов по депозитам/revenue;
  - ranked хуки (депозиты раскидываются по visual_hooks/text_hook креатива);
  - ranked англы (по sub6).

Двойной URL-энкодинг макросов нормализуется (M-Pesa+angle == M-Pesa angle).
Пока постбэков нет — печатает библиотечную сводку (хуки/креативы/референсы).

Запуск:
    python scripts/creative_report.py            # отчёт за 30 дней в stdout
    python scripts/creative_report.py --days 7
    python scripts/creative_report.py --write     # + записать docs/creatives/_report.md
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote_plus

from sqlalchemy import text

from core.creatives.registry import DEFAULT_REGISTRY_DIR, Registry, load_registry
from core.db import get_engine


def _norm(value: object) -> str:
    """Нормализует sub-поле: двойной URL-декод + lower + strip (схлопывает энкодинг-дубли)."""
    return unquote_plus(unquote_plus(str(value or ""))).strip().lower()


async def _load_deposits(days: int, *, engine=None, now: datetime | None = None) -> list[dict]:
    """Confirmed registration+FTD clicks per creative tags for the period."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    event_floor = since - timedelta(days=2)
    sql = text(
        """
        SELECT tags.sub3,
               tags.sub6,
               COUNT(*) AS deposits,
               COALESCE(SUM(s.ftd_revenue), 0) AS revenue
        FROM tracker_click_state s
        JOIN LATERAL (
            SELECT e.raw_json->>'sub3' AS sub3,
                   e.raw_json->>'sub6' AS sub6
            FROM adsetpro_postback_events e
            WHERE e.source = s.source
              AND e.click_id = s.click_id
              AND e.is_duplicate = false
              AND e.attribution_status <> 'ambiguous'
              AND e.received_at >= :event_floor
            ORDER BY e.received_at DESC, e.id DESC
            LIMIT 1
        ) tags ON true
        WHERE s.confirmed_deposit = true
          AND s.confirmed_deposit_at >= :since
        GROUP BY 1, 2
        """
    )
    query_engine = engine or get_engine()
    async with query_engine.begin() as conn:
        result = await conn.execute(sql, {"since": since, "event_floor": event_floor})
        return [dict(row._mapping) for row in result]


def _build_report(reg: Registry, deposits: list[dict], days: int) -> str:
    """Собирает markdown-отчёт из реестра и строк депозитов."""
    lines: list[str] = []
    lines.append("# Creative Report")
    lines.append(f"\nПериод: последние **{days} дн.** · депозит = регистрация + FTD\n")

    errors = reg.validate()
    if errors:
        lines.append("## ⚠️ Ошибки реестра\n")
        lines.extend(f"- {e}" for e in errors)
        lines.append("")

    # Индекс креативов по нормализованному code
    by_code = {_norm(c.code): c for c in reg.all_creatives()}

    total_deposits = sum(int(r["deposits"]) for r in deposits)

    if total_deposits == 0:
        # Библиотечный режим — данных трекера ещё нет
        lines.append("## Библиотека (трекер пуст — данные появятся после заливов)\n")
        hooks_by_verdict: dict[str, int] = defaultdict(int)
        for h in reg.hooks.values():
            hooks_by_verdict[h.verdict] += 1
        cr_by_status: dict[str, int] = defaultdict(int)
        for c in reg.all_creatives():
            cr_by_status[c.status] += 1
        refs = sum(len(s.references) for g in reg.geos.values() for s in g.slots.values())
        lines.append(f"- Хуков: **{len(reg.hooks)}** ({_fmt_counts(hooks_by_verdict)})")
        lines.append(f"- Креативов: **{len(reg.all_creatives())}** ({_fmt_counts(cr_by_status)})")
        lines.append(f"- Референсов: **{refs}**")
        lines.append(
            f"- Гео: {', '.join(reg.geos)} · слотов: {sum(len(g.slots) for g in reg.geos.values())}"
        )
        lines.append("\n### Хуки-победители (по опыту, ещё не из этого окна)\n")
        winners = [h for h in reg.hooks.values() if h.verdict == "winner"]
        for h in winners:
            lines.append(f"- **{h.id}** ({h.level}) — {h.text}")
            if h.evidence:
                lines.append(f"  - _{h.evidence}_")
        return "\n".join(lines) + "\n"

    # Leaderboard креативов
    cr_dep: dict[str, dict] = defaultdict(lambda: {"deposits": 0, "revenue": 0.0})
    angle_dep: dict[str, int] = defaultdict(int)
    hook_dep: dict[str, int] = defaultdict(int)
    unknown_codes: dict[str, int] = defaultdict(int)

    for row in deposits:
        dep = int(row["deposits"])
        rev = float(row["revenue"] or 0)
        code_n = _norm(row["sub3"])
        angle_n = _norm(row["sub6"])
        if angle_n:
            angle_dep[angle_n] += dep
        cr = by_code.get(code_n)
        if cr is None:
            unknown_codes[row["sub3"] or "—"] += dep
            continue
        cr_dep[cr.code]["deposits"] += dep
        cr_dep[cr.code]["revenue"] += rev
        # Раскидать депозиты по хукам креатива
        for hid in cr.all_hook_ids():
            hook_dep[hid] += dep

    lines.append("## Leaderboard креативов\n")
    lines.append("| Креатив | Депозиты | Revenue | Вердикт |")
    lines.append("|---|--:|--:|---|")
    for code, agg in sorted(cr_dep.items(), key=lambda kv: kv[1]["deposits"], reverse=True):
        cr = reg.find_creative(code)
        verdict = cr.verdict if cr else "?"
        lines.append(f"| {code} | {agg['deposits']} | {agg['revenue']:.2f} | {verdict} |")
    if unknown_codes:
        lines.append("")
        lines.append(
            "> ⚠️ Депозиты с code вне реестра (добавь креатив в YAML): "
            + ", ".join(f"{k} ({v})" for k, v in unknown_codes.items())
        )

    # Ranked хуки
    lines.append("\n## Ranked хуки (депозиты через креативы)\n")
    lines.append("| Хук | Уровень | Депозиты | Вердикт реестра |")
    lines.append("|---|---|--:|---|")
    for hid, dep in sorted(hook_dep.items(), key=lambda kv: kv[1], reverse=True):
        hook = reg.hooks.get(hid)
        level = hook.level if hook else "?"
        verdict = hook.verdict if hook else "?"
        lines.append(f"| {hid} | {level} | {dep} | {verdict} |")

    # Ranked англы (sub6)
    lines.append("\n## Ranked англы (sub6)\n")
    lines.append("| Угол (sub6, норм.) | Депозиты |")
    lines.append("|---|--:|")
    for angle, dep in sorted(angle_dep.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"| {angle} | {dep} |")

    return "\n".join(lines) + "\n"


def _fmt_counts(counts: dict[str, int]) -> str:
    """`winner: 3, works: 2` для сводки."""
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


async def _main_async(days: int, write: bool, base_dir: Path) -> None:
    reg = load_registry(base_dir)
    try:
        deposits = await _load_deposits(days)
    except Exception as exc:  # noqa: BLE001
        # БД недоступна — всё равно отдаём библиотечный отчёт
        print(f"[инфо] трекер недоступен ({type(exc).__name__}), библиотечный режим\n")
        deposits = []
    report = _build_report(reg, deposits, days)
    print(report)
    if write:
        out = base_dir / "_report.md"
        out.write_text(report, encoding="utf-8")
        print(f"[записано] {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Creative Report: реестр ⨝ трекер")
    parser.add_argument("--days", type=int, default=30, help="окно депозитов (дни)")
    parser.add_argument("--write", action="store_true", help="записать docs/creatives/_report.md")
    parser.add_argument(
        "--base-dir", type=Path, default=DEFAULT_REGISTRY_DIR, help="корень реестра"
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args.days, args.write, args.base_dir))


if __name__ == "__main__":
    main()
