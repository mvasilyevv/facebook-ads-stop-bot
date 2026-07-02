# -*- coding: utf-8 -*-
"""Длина alembic revision id ≤ 32 символов (колонка alembic_version — varchar(32)).

Инцидент деплоя 02.07: ревизия '0032_tracker_aggregate_revenue_precision'
(40 символов) падала StringDataRightTruncationError на UPDATE alembic_version —
прод откатился. CI это не ловит (тесты не гоняют alembic upgrade), поэтому
контракт фиксируем статически по всем файлам миграций.
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
