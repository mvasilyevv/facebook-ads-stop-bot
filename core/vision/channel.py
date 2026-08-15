"""Человекочитаемая диагностика канала Vision для операторских поверхностей."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.vision.cloud_probe import VisionCloudProbeState

VisionChannelReason = Literal[
    "TOKEN_MISSING",
    "TOKEN_REJECTED",
    "CLOUD_CREDENTIALS_MISSING",
    "PROFILE_NOT_CONFIGURED",
    "PROFILE_NOT_FOUND",
    "CLOUD_UNAVAILABLE",
    "BROWSER_UNAVAILABLE",
    "READY",
    "UNKNOWN",
]
VisionChannelStatus = Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class VisionChannelAssessment:
    status: VisionChannelStatus
    reason: VisionChannelReason
    message: str
    next_step: str


def assess_vision_channel(
    *,
    has_token: bool,
    profile_configured: bool,
    has_cloud_credentials: bool,
    cloud_state: VisionCloudProbeState | None,
    browser_status: VisionChannelStatus,
) -> VisionChannelAssessment:
    """Выбрать безопасную причину и следующий шаг без внутренних деталей."""

    if not has_token:
        return VisionChannelAssessment(
            "UNAVAILABLE",
            "TOKEN_MISSING",
            "Токен Vision не задан.",
            "Введите X-Token и сохраните настройки.",
        )
    if not profile_configured:
        return VisionChannelAssessment(
            "UNAVAILABLE",
            "PROFILE_NOT_CONFIGURED",
            "Профиль Vision не задан.",
            "Введите Profile ID и сохраните настройки.",
        )
    # Подтверждённый браузерный канал старше любой облачной диагностики: эта
    # проба сходила в живую сессию и выполнила настоящий запрос к Graph. Если
    # пропустить её вперёд облака, недоступное облако объявит мёртвым канал,
    # который прямо сейчас работает.
    if browser_status == "READY":
        return VisionChannelAssessment(
            "READY",
            "READY",
            "Канал Vision жив: профиль и браузер подтверждены живым запросом.",
            "Действий не требуется.",
        )
    if cloud_state == "token_rejected":
        if not has_cloud_credentials:
            return VisionChannelAssessment(
                "DEGRADED",
                "CLOUD_CREDENTIALS_MISSING",
                "Облако Vision отвергает сохранённый токен: он истёк или отозван. "
                "Логин и пароль для автоматического обновления не заданы.",
                "Введите логин и пароль Vision; при необходимости укажите team id и folder id, "
                "затем сохраните.",
            )
        return VisionChannelAssessment(
            "DEGRADED",
            "TOKEN_REJECTED",
            "Облако Vision отвергает сохранённый токен: он истёк или отозван.",
            "Проверьте логин, пароль и параметры команды, сохраните их и повторите проверку.",
        )
    if cloud_state == "profile_not_found":
        return VisionChannelAssessment(
            "DEGRADED",
            "PROFILE_NOT_FOUND",
            "Профиль Vision настроен, но в облаке он не найден.",
            "Проверьте Profile ID и folder id, затем сохраните настройки.",
        )
    if cloud_state == "unavailable" or cloud_state is None:
        return VisionChannelAssessment(
            "UNAVAILABLE",
            "CLOUD_UNAVAILABLE",
            "Не удалось проверить облако Vision.",
            "Повторите проверку позже; при необходимости проверьте доступность Vision.",
        )
    if browser_status == "READY":
        return VisionChannelAssessment(
            "READY",
            "READY",
            "Канал Vision жив: облако, профиль и браузер подтверждены.",
            "Действий не требуется.",
        )
    if browser_status in {"DEGRADED", "UNAVAILABLE"}:
        return VisionChannelAssessment(
            browser_status,
            "BROWSER_UNAVAILABLE",
            "Облако и профиль Vision доступны, но рабочий браузерный канал не подтверждён.",
            "Нажмите «Переподключить Vision» и повторите проверку.",
        )
    return VisionChannelAssessment(
        "UNKNOWN",
        "UNKNOWN",
        "Готовность канала Vision не подтверждена.",
        "Проверьте настройки и повторите проверку.",
    )


__all__ = [
    "VisionChannelAssessment",
    "VisionChannelReason",
    "VisionChannelStatus",
    "assess_vision_channel",
]
