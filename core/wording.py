# -*- coding: utf-8 -*-
"""Человеческие формулировки для операторских карточек и уведомлений.

Один источник склонения числительных и русских названий операций. Тексты
Telegram-карточек собираются только из этих хелперов, иначе «43 кликов»,
«1 объявлений» и «3 рег» расползаются по воркерам и правятся поштучно.

Модуль намеренно без зависимостей: его импортируют observer, task queue,
meta_api, rules и digest, поэтому он не должен тянуть за собой слои.
"""

from __future__ import annotations

_TEEN_REMAINDERS = frozenset(range(11, 15))
_FEW_REMAINDERS = frozenset({2, 3, 4})


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """Форма слова, согласованная с числом: 1 клик / 2 клика / 5 кликов."""
    amount = abs(int(count))
    if amount % 100 in _TEEN_REMAINDERS:
        return many
    remainder = amount % 10
    if remainder == 1:
        return one
    if remainder in _FEW_REMAINDERS:
        return few
    return many


def counted_ru(count: int, one: str, few: str, many: str) -> str:
    """Число вместе с согласованным словом: «5 кликов»."""
    return f"{int(count)} {plural_ru(count, one, few, many)}"


def clicks_ru(count: int) -> str:
    """«43 клика»; подтверждённый ноль читается словами, а не как «0 кликов»."""
    if int(count) == 0:
        return "кликов нет"
    return counted_ru(count, "клик", "клика", "кликов")


def leads_ru(count: int) -> str:
    """«2 лида» или «лидов нет»."""
    if int(count) == 0:
        return "лидов нет"
    return counted_ru(count, "лид", "лида", "лидов")


def registrations_ru(count: int) -> str:
    """«3 регистрации» или «регистраций нет»."""
    if int(count) == 0:
        return "регистраций нет"
    return counted_ru(count, "регистрация", "регистрации", "регистраций")


def deposits_ru(count: int) -> str:
    """«2 депозита» или «депозитов нет»."""
    if int(count) == 0:
        return "депозитов нет"
    return counted_ru(count, "депозит", "депозита", "депозитов")


def ads_ru(count: int) -> str:
    """«5 объявлений»."""
    return counted_ru(count, "объявление", "объявления", "объявлений")


def campaigns_ru(count: int) -> str:
    """«2 кампании»."""
    return counted_ru(count, "кампания", "кампании", "кампаний")


def adsets_ru(count: int) -> str:
    """«3 адсета»."""
    return counted_ru(count, "адсет", "адсета", "адсетов")


def creatives_ru(count: int) -> str:
    """«3 креатива»."""
    return counted_ru(count, "креатив", "креатива", "креативов")


def offers_ru(count: int) -> str:
    """«2 оффера»."""
    return counted_ru(count, "оффер", "оффера", "офферов")


def minutes_ru(count: int) -> str:
    """«12 минут»."""
    return counted_ru(count, "минута", "минуты", "минут")


def hours_ru(count: int) -> str:
    """«25 часов»."""
    return counted_ru(count, "час", "часа", "часов")


def days_ru(count: int) -> str:
    """«3 дня»."""
    return counted_ru(count, "день", "дня", "дней")


def human_bytes_ru(value: int) -> str:
    """Компактный двоичный размер: «5 ГиБ», «1,5 ТиБ»."""
    amount = max(0, int(value))
    if amount < 1024:
        return counted_ru(amount, "байт", "байта", "байт")
    units = ("КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ")
    scaled = float(amount)
    unit = units[0]
    for candidate in units:
        scaled /= 1024
        unit = candidate
        if scaled < 1024 or candidate == units[-1]:
            break
    rendered = f"{scaled:.0f}" if scaled >= 10 or scaled.is_integer() else f"{scaled:.1f}"
    return f"{rendered.replace('.', ',')} {unit}"


def objects_ru(count: int) -> str:
    """«3 объекта» — про созданные в Meta сущности."""
    return counted_ru(count, "объект", "объекта", "объектов")


def warnings_ru(count: int) -> str:
    """«2 предупреждения»."""
    return counted_ru(count, "предупреждение", "предупреждения", "предупреждений")


def commands_ru(count: int) -> str:
    """«2 команды» — про действия, отправленные в Facebook."""
    return counted_ru(count, "команда", "команды", "команд")


def times_ru(count: int) -> str:
    """«3 раза» — про повторы подряд."""
    return counted_ru(count, "раз", "раза", "раз")


def errors_ru(count: int) -> str:
    """«2 ошибки»."""
    return counted_ru(count, "ошибка", "ошибки", "ошибок")


# Русские названия операций Meta: оператор видит их в карточке вместо
# внутреннего mutation_kind. Ключи совпадают с core.meta_api.schemas.MUTATION_KINDS.
_ACTION_LABELS: dict[str, str] = {
    "pause_ad": "отключение объявления",
    "activate_ad": "включение объявления",
    "bulk_status_change": "массовое изменение статуса",
    "duplicate_adset_structure": "дублирование адсетов",
}


def action_label_ru(kind: str) -> str:
    """Название операции для карточки; неизвестный kind остаётся как есть."""
    normalized = (kind or "").strip()
    return _ACTION_LABELS.get(normalized, normalized or "действие")


# Статус доставки объявления в Meta: оператор читает «включено», а не ACTIVE.
_DELIVERY_STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "включённое",
    "WITH_ISSUES": "включённое с замечаниями",
    "PAUSED": "выключенное",
    "OFF": "выключенное",
    "ADSET_PAUSED": "выключенное адсетом",
    "CAMPAIGN_PAUSED": "выключенное кампанией",
    "PENDING_REVIEW": "ожидающее модерации",
    "IN_REVIEW": "ожидающее модерации",
    "DISAPPROVED": "отклонённое",
    "NOT_DELIVERING": "не доставляющееся",
    "DELETED": "удалённое",
    "ARCHIVED": "архивное",
}


def delivery_status_ru(status: str) -> str:
    """Русское описание статуса объявления; неизвестный статус остаётся как есть."""
    normalized = (status or "").strip().upper()
    return _DELIVERY_STATUS_LABELS.get(normalized, normalized or "неизвестное")


__all__ = [
    "action_label_ru",
    "ads_ru",
    "adsets_ru",
    "campaigns_ru",
    "clicks_ru",
    "commands_ru",
    "counted_ru",
    "creatives_ru",
    "days_ru",
    "delivery_status_ru",
    "deposits_ru",
    "errors_ru",
    "hours_ru",
    "human_bytes_ru",
    "leads_ru",
    "minutes_ru",
    "objects_ru",
    "offers_ru",
    "plural_ru",
    "registrations_ru",
    "times_ru",
    "warnings_ru",
]
