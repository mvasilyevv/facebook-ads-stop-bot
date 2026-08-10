# -*- coding: utf-8 -*-
"""Alembic revision IDs fit the default ``alembic_version`` varchar(32).

This protects deployment before PostgreSQL can reject an overlong revision
during the version-table update. CI also executes the fresh baseline itself.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Лимит колонки alembic_version.version_num (дефолт alembic — VARCHAR(32)).
MAX_REVISION_LEN = 32


# Каждая ревизия в migrations/versions влезает в varchar(32) alembic_version
def test_all_revision_ids_fit_version_column():
    files = sorted(VERSIONS_DIR.glob("*.py"))
    assert files, "не найдены файлы миграций"
    violations: list[str] = []
    for f in files:
        m = re.search(
            r'^revision(?:\s*:\s*str)?\s*=\s*["\']([^"\']+)["\']', f.read_text(), re.MULTILINE
        )
        assert m, f"в {f.name} не найдена строка revision = ..."
        rev = m.group(1)
        if len(rev) > MAX_REVISION_LEN:
            violations.append(f"{f.name}: '{rev}' ({len(rev)} символов)")
    assert not violations, (
        f"revision id длиннее {MAX_REVISION_LEN} символов (упадёт UPDATE alembic_version): "
        + "; ".join(violations)
    )


def test_fresh_baseline_emits_targetable_campaign_run_events() -> None:
    sql = (VERSIONS_DIR / "0001_safety_first_baseline.sql").read_text(encoding="utf-8")

    assert (
        "CREATE TRIGGER trg_campaign_run_operator_notify "
        "AFTER INSERT OR DELETE OR UPDATE ON public.campaign_run "
        "FOR EACH ROW EXECUTE FUNCTION "
        "public.notify_fb_operator_event('campaign_run', 'id');"
    ) in sql
