# -*- coding: utf-8 -*-
"""Предикаты канала отключения/включения рекламы после удаления DOM-канала.

Отключение/включение идёт через Marketing API: task_type='meta_api_mutation'
с mutation_kind='pause_ad'/'activate_ad'. Старый DOM-канал (task_type='disable'/
'enable') удалён, но в БД могут долёживать исторические записи — фолбэк на них
сохраняем (единообразно с core/telegram/digest_builder.py).

fb_ad_id у meta-мутации лежит в payload->>'target_id'; у legacy-задач — в
payload->>'fb_ad_id'. target_id_sql() резолвит оба через COALESCE.

Литералы mutation_kind/task_type — наши константы, не пользовательский ввод,
поэтому подставляются в SQL напрямую (инъекции нет). Единая точка, чтобы и
apps/api, и core/dashboard читали канал одинаково.
"""

from __future__ import annotations

PAUSE_KIND = "pause_ad"
ACTIVATE_KIND = "activate_ad"


def disable_channel_sql(alias: str = "tq") -> str:
    """WHERE-предикат для disable-задач (Marketing API pause_ad + legacy disable)."""
    return (
        f"(({alias}.task_type = 'meta_api_mutation' "
        f"AND {alias}.payload->>'mutation_kind' = 'pause_ad') "
        f"OR {alias}.task_type = 'disable')"
    )


def enable_channel_sql(alias: str = "tq") -> str:
    """WHERE-предикат для enable-задач (Marketing API activate_ad + legacy enable)."""
    return (
        f"(({alias}.task_type = 'meta_api_mutation' "
        f"AND {alias}.payload->>'mutation_kind' = 'activate_ad') "
        f"OR {alias}.task_type = 'enable')"
    )


def target_id_sql(alias: str = "tq") -> str:
    """fb_ad_id из payload: target_id (meta-мутация) с фолбэком на legacy fb_ad_id."""
    return f"COALESCE({alias}.payload->>'target_id', {alias}.payload->>'fb_ad_id')"


def is_disable_row(task_type: str | None, mutation_kind: str | None) -> bool:
    """True если строка task_queue — задача отключения (новый канал или legacy)."""
    return task_type == "disable" or (
        task_type == "meta_api_mutation" and mutation_kind == "pause_ad"
    )


def is_enable_row(task_type: str | None, mutation_kind: str | None) -> bool:
    """True если строка task_queue — задача включения (новый канал или legacy)."""
    return task_type == "enable" or (
        task_type == "meta_api_mutation" and mutation_kind == "activate_ad"
    )


__all__ = [
    "ACTIVATE_KIND",
    "PAUSE_KIND",
    "disable_channel_sql",
    "enable_channel_sql",
    "is_disable_row",
    "is_enable_row",
    "target_id_sql",
]
