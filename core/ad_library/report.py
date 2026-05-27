# -*- coding: utf-8 -*-
"""Report builder — финальный markdown-отчёт по scan'у."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def build_report(
    engine: AsyncEngine,
    *,
    scan_id: str,
    top_n: int = 10,
) -> dict[str, Any]:
    """Строит финальный отчёт: top winners + vertical breakdown + markdown.

    Returns: dict со всеми ключами для INSERT в ad_library_report.
    """
    async with engine.connect() as conn:
        scan_meta = (
            await conn.execute(
                text(
                    """
                    SELECT slot, country, ads_count, duration_ms, started_at
                    FROM ad_library_scan WHERE id = :sid
                    """
                ),
                {"sid": scan_id},
            )
        ).first()
        if not scan_meta:
            raise RuntimeError(f"scan {scan_id} не найден")

        slot, country, ads_count, duration_ms, started_at = scan_meta

        # Tier breakdown
        tier_breakdown_rows = (
            await conn.execute(
                text(
                    "SELECT tier, COUNT(*) FROM ad_library_tier WHERE scan_id = :sid GROUP BY tier"
                ),
                {"sid": scan_id},
            )
        ).all()
        tier_breakdown = {t: n for t, n in tier_breakdown_rows}

        # Vertical breakdown
        vertical_breakdown_rows = (
            await conn.execute(
                text(
                    """
                    SELECT COALESCE(a.vertical, 'unknown') as v, COUNT(*)
                    FROM ad_library_snapshot s
                    JOIN ad_library_ad a ON a.ad_archive_id = s.ad_archive_id
                    WHERE s.scan_id = :sid
                    GROUP BY v
                    """
                ),
                {"sid": scan_id},
            )
        ).all()
        vertical_breakdown = {v: n for v, n in vertical_breakdown_rows}

        # Top N — сортировка по tier (S>A>B>C) + score DESC
        top_rows = (
            await conn.execute(
                text(
                    """
                    SELECT
                        t.tier,
                        t.score,
                        t.ad_archive_id,
                        a.page_name,
                        a.page_id,
                        a.started_running_on,
                        a.ad_format,
                        a.vertical,
                        a.ai_summary,
                        t.reason_json
                    FROM ad_library_tier t
                    JOIN ad_library_ad a ON a.ad_archive_id = t.ad_archive_id
                    WHERE t.scan_id = :sid
                    ORDER BY
                        CASE t.tier WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
                        t.score DESC
                    LIMIT :n
                    """
                ),
                {"sid": scan_id, "n": top_n},
            )
        ).all()

    top_winners = []
    for row in top_rows:
        (
            tier,
            score,
            ad_archive_id,
            page_name,
            page_id,
            started,
            ad_format,
            vertical,
            ai_summary,
            reason,
        ) = row
        top_winners.append(
            {
                "tier": tier,
                "score": round(float(score), 4),
                "ad_archive_id": ad_archive_id,
                "page_name": page_name,
                "page_id": page_id,
                "started_running_on": str(started) if started else None,
                "ad_format": ad_format,
                "vertical": vertical,
                "ai_summary": ai_summary,
                "tier_reason": reason,
            }
        )

    # Markdown
    md_lines = [
        f"# Ad Library Report — {slot} / {country}",
        "",
        f"Scan: `{scan_id}`",
        f"Started: {started_at}",
        f"Ads collected: **{ads_count}**",
        f"Duration: {duration_ms} ms",
        "",
        "## Tier Breakdown",
        "",
    ]
    for t in ("S", "A", "B", "C"):
        md_lines.append(f"- **{t}-tier**: {tier_breakdown.get(t, 0)}")
    md_lines.append("")
    md_lines.append("## Vertical Breakdown")
    md_lines.append("")
    for v, n in sorted(vertical_breakdown.items(), key=lambda kv: -kv[1]):
        md_lines.append(f"- **{v}**: {n}")
    md_lines.append("")
    md_lines.append(f"## Top {top_n} winners")
    md_lines.append("")
    for i, w in enumerate(top_winners, start=1):
        md_lines.append(
            f"{i}. **[{w['tier']}]** `{w['ad_archive_id']}` — "
            f"{w['page_name']} (score={w['score']}, "
            f"vertical={w['vertical'] or '?'}, "
            f"running since {w['started_running_on'] or 'unknown'})"
        )
        if w.get("ai_summary"):
            hooks = w["ai_summary"].get("hooks") or []
            ctas = w["ai_summary"].get("ctas") or []
            tone = w["ai_summary"].get("tone")
            md_lines.append(f"   - hooks: {hooks}, ctas: {ctas}, tone: {tone}")
    md_lines.append("")
    markdown_report = "\n".join(md_lines)

    payload = {
        "scan_id": scan_id,
        "top_winners_json": top_winners,
        "vertical_breakdown_json": vertical_breakdown,
        "markdown_report": markdown_report,
    }

    # Сохраняем в БД
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_library_report
                    (scan_id, top_winners_json, vertical_breakdown_json, markdown_report)
                VALUES
                    (:sid, CAST(:tw AS JSONB), CAST(:vb AS JSONB), :md)
                ON CONFLICT (scan_id) DO UPDATE
                SET top_winners_json = EXCLUDED.top_winners_json,
                    vertical_breakdown_json = EXCLUDED.vertical_breakdown_json,
                    markdown_report = EXCLUDED.markdown_report,
                    generated_at = NOW()
                """
            ),
            {
                "sid": scan_id,
                "tw": json.dumps(top_winners),
                "vb": json.dumps(vertical_breakdown),
                "md": markdown_report,
            },
        )

    logger.info("Report built for scan %s (%d top winners)", scan_id, len(top_winners))
    return payload
