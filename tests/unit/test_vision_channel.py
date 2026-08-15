from __future__ import annotations

import pytest

from core.vision.channel import assess_vision_channel


@pytest.mark.parametrize(
    ("kwargs", "reason", "status", "message_fragment", "next_fragment"),
    [
        (
            {
                "has_token": False,
                "profile_configured": False,
                "has_cloud_credentials": False,
                "cloud_state": None,
                "browser_status": "UNKNOWN",
            },
            "TOKEN_MISSING",
            "UNAVAILABLE",
            "Токен Vision не задан",
            "Введите X-Token",
        ),
        (
            {
                "has_token": True,
                "profile_configured": True,
                "has_cloud_credentials": True,
                "cloud_state": "token_rejected",
                "browser_status": "UNKNOWN",
            },
            "TOKEN_REJECTED",
            "DEGRADED",
            "облако Vision отвергает",
            "Проверьте логин",
        ),
        (
            {
                "has_token": True,
                "profile_configured": True,
                "has_cloud_credentials": False,
                "cloud_state": "token_rejected",
                "browser_status": "UNKNOWN",
            },
            "CLOUD_CREDENTIALS_MISSING",
            "DEGRADED",
            "Логин и пароль",
            "Введите логин и пароль",
        ),
        (
            {
                "has_token": True,
                "profile_configured": True,
                "has_cloud_credentials": True,
                "cloud_state": "profile_not_found",
                "browser_status": "UNKNOWN",
            },
            "PROFILE_NOT_FOUND",
            "DEGRADED",
            "в облаке он не найден",
            "Проверьте Profile ID",
        ),
        (
            {
                "has_token": True,
                "profile_configured": True,
                "has_cloud_credentials": True,
                "cloud_state": "ready",
                "browser_status": "READY",
            },
            "READY",
            "READY",
            "Канал Vision жив",
            "Действий не требуется",
        ),
    ],
)
def test_vision_channel_selects_actionable_copy(
    kwargs: dict[str, object],
    reason: str,
    status: str,
    message_fragment: str,
    next_fragment: str,
) -> None:
    assessment = assess_vision_channel(**kwargs)  # type: ignore[arg-type]

    assert assessment.reason == reason
    assert assessment.status == status
    assert message_fragment.casefold() in assessment.message.casefold()
    assert next_fragment.casefold() in assessment.next_step.casefold()
