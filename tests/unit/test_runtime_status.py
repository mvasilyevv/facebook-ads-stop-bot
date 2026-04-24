# -*- coding: utf-8 -*-
"""Тесты форматирования runtime-ошибок observer для UI."""

from core.observer.runtime_status import format_observer_runtime_message


# Проверяем, что UI получает понятную причину, когда автоперезапуск профиля осознанно выключен.
def test_format_runtime_message_for_missing_cdp_with_restart_disabled():
    message = format_observer_runtime_message(
        "Профиль abc запущен без CDP-порта. "
        "Автоперезапуск профиля для восстановления CDP-порта отключён. "
        "Включите VISION_AUTO_RESTART_ON_MISSING_CDP=true или перезапустите профиль вручную."
    )

    assert message == (
        "Vision запустил профиль без CDP-порта. "
        "Автоперезапуск выключен, поэтому профиль нужно перезапустить вручную "
        "или явно включить feature flag."
    )


# Проверяем, что неудачный автоперезапуск превращается в понятное сообщение для UI.
def test_format_runtime_message_for_failed_cdp_auto_restart():
    message = format_observer_runtime_message(
        "Не удалось восстановить CDP-порт автоперезапуском профиля abc: "
        "Профиль abc не остановился перед перезапуском для восстановления CDP-порта"
    )

    assert message == (
        "Vision не смог восстановить CDP-порт автоматическим перезапуском профиля. "
        "Проверьте профиль вручную и запустите его заново."
    )
