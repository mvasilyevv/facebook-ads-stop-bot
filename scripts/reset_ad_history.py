from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db import get_session_factory  # noqa: E402
from core.services import preview_advertising_history_reset, reset_advertising_history  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Очищает рекламную историю из базы данных с безопасным подтверждением."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Выполнить очистку. Без этого флага команда покажет только превью.",
    )
    return parser


def _format_report_lines(*, is_confirmed: bool, report_total: int, table_rows: list[str]) -> str:
    title = (
        "Очистка рекламной истории завершена."
        if is_confirmed
        else "Превью очистки рекламной истории."
    )
    preserved = (
        "Сохраняются: offers, offer_rate_versions, rule_sets, rules, "
        "system_settings, browser_hosts, profiles, browser_sessions, worker_heartbeats."
    )
    lines = [
        title,
        *table_rows,
        f"Всего записей: {report_total}",
        preserved,
        "Перед реальной очисткой остановите worker, чтобы история сразу не начала заполняться заново.",
    ]
    if not is_confirmed:
        lines.append("Для выполнения очистки повторите команду с флагом --confirm.")
    return "\n".join(lines)


async def _run(confirm: bool) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        report = (
            await reset_advertising_history(session)
            if confirm
            else await preview_advertising_history_reset(session)
        )

    table_rows = [f"- {stat.table_name}: {stat.rows}" for stat in report.table_stats]
    print(
        _format_report_lines(
            is_confirmed=confirm,
            report_total=report.total_rows,
            table_rows=table_rows,
        )
    )
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(confirm=args.confirm))
    except KeyboardInterrupt:
        print("Очистка прервана пользователем.", file=sys.stderr)
        return 130
    except Exception as error:  # noqa: BLE001
        print(f"Не удалось выполнить очистку рекламной истории: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
