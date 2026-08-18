# -*- coding: utf-8 -*-
"""Скоуп «наших кабинетов» определяется ровно в одном месте.

17.08.2026 в core/meta_api/account_tz.py завёлся второй ответ на вопрос «какие
кабинеты наши» — DISTINCT по fb_campaigns. Он выглядел безобидно, но замкнул
круг: новый кабинет не получал снимок контекста никогда. Тест ловит любую
попытку снова вывести скоуп из следов сканера.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Таблицы, которые наполняет сканер. Скоуп из них выводить нельзя: это
# производная от работы сканера, а не намерение оператора.
_SCAN_TABLES = ("fb_campaigns", "fb_adsets", "ad_metrics")

# Ловим именно ВЫБОРКУ МНОЖЕСТВА кабинетов. Чтение каталога сканов ради одной
# идентичности (load_ad_account_id_for_fb_ad: кабинет конкретного fb_ad_id)
# сюда не попадает и не нуждается в исключении: там нет DISTINCT. Список
# исключений намеренно отсутствует — вечное исключение ослабило бы гард.
_SCOPE_QUERY = re.compile(
    r"DISTINCT\s+ad_account_id|distinct\(\s*[A-Za-z_.]*ad_account_id",
    re.IGNORECASE,
)


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for folder in ("core", "apps"):
        files.extend(
            path for path in (ROOT / folder).rglob("*.py") if "__pycache__" not in path.parts
        )
    return files


def test_no_module_derives_cabinet_scope_from_scan_results() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        if not _SCOPE_QUERY.search(source):
            continue
        if any(table in source for table in _SCAN_TABLES):
            offenders.append(str(relative))
    assert offenders == [], (
        "скоуп кабинетов выводится из следов сканера в: "
        + ", ".join(offenders)
        + " — используйте резолвер конфигурации из core/observer/accounts.py"
    )


def test_configured_scope_sql_lives_in_exactly_one_module() -> None:
    """Членство кабинета в оффере читает только каталог."""
    owners = [
        str(path.relative_to(ROOT))
        for path in _python_sources()
        if "OfferAdAccount" in path.read_text(encoding="utf-8")
        and "models" not in path.relative_to(ROOT).parts
    ]
    assert owners == ["core/ad_account_catalog.py"]
