# -*- coding: utf-8 -*-
"""Тесты для disable worker: retry-логика, подтверждение OFF и backoff."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertState, DisableTaskStatus


@dataclass
class FakeDisableTask:
    """Фейковая задача для тестов."""

    id: str = "task-001"
    fb_ad_id: str = "123456"
    ad_name: str = "Тестовое объявление"
    attempt_count: int = 0
    max_attempts: int = 10
    requested_by_username: str = "tester"
    status: str = DisableTaskStatus.RUNNING


def _compute_backoff_delay(attempt: int) -> int:
    """Расчёт задержки экспоненциального backoff (как в disable_worker_loop)."""
    return min(30 * (2 ** max(attempt - 1, 0)), 300)


# Тест: задача с attempt >= max_attempts получает статус FAILED
@pytest.mark.asyncio
async def test_task_marked_failed_when_attempts_exhausted():
    """Проверяем, что задача помечается как FAILED при исчерпании попыток."""
    from apps.disable_worker.main import disable_worker_loop

    task = FakeDisableTask(attempt_count=10, max_attempts=10)
    call_count = 0

    async def claim_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        return None

    mark_succeeded = AsyncMock()
    mark_retrying = AsyncMock()
    mark_failed = AsyncMock()

    # execute_disable всегда провал
    execute_disable = AsyncMock(return_value=(False, "Элемент не найден"))

    loop_task = asyncio.create_task(
        disable_worker_loop(
            poll_interval_seconds=0,
            claim_next_task=claim_once,
            execute_disable=execute_disable,
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            mark_failed=mark_failed,
        )
    )

    # Даём циклу обработать одну задачу
    await asyncio.sleep(0.1)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    # mark_failed вызван с правильными аргументами
    mark_failed.assert_called_once_with(task.id, "Элемент не найден")
    mark_retrying.assert_not_called()
    mark_succeeded.assert_not_called()


# Тест: задача с attempt < max_attempts получает статус RETRYING
@pytest.mark.asyncio
async def test_task_marked_retrying_when_attempts_remaining():
    """Проверяем, что задача помечается как RETRYING если попытки не исчерпаны."""
    from apps.disable_worker.main import disable_worker_loop

    task = FakeDisableTask(attempt_count=3, max_attempts=10)
    call_count = 0

    async def claim_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        return None

    mark_succeeded = AsyncMock()
    mark_retrying = AsyncMock()
    mark_failed = AsyncMock()

    execute_disable = AsyncMock(return_value=(False, "Таймаут"))

    loop_task = asyncio.create_task(
        disable_worker_loop(
            poll_interval_seconds=0,
            claim_next_task=claim_once,
            execute_disable=execute_disable,
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            mark_failed=mark_failed,
        )
    )

    await asyncio.sleep(0.1)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    # mark_retrying вызван, mark_failed — нет
    mark_retrying.assert_called_once()
    call_args = mark_retrying.call_args
    assert call_args[0][0] == task.id
    assert call_args[0][1] == "Таймаут"
    mark_failed.assert_not_called()
    mark_succeeded.assert_not_called()


# Тест: экспоненциальный backoff — проверяем расчёт задержки
def test_exponential_backoff_delays():
    """Проверяем формулу экспоненциального backoff: 30s, 60s, 120s, 240s, 300s (макс)."""
    # attempt=1 → 30 * 2^0 = 30
    assert _compute_backoff_delay(1) == 30
    # attempt=2 → 30 * 2^1 = 60
    assert _compute_backoff_delay(2) == 60
    # attempt=3 → 30 * 2^2 = 120
    assert _compute_backoff_delay(3) == 120
    # attempt=4 → 30 * 2^3 = 240
    assert _compute_backoff_delay(4) == 240
    # attempt=5 → 30 * 2^4 = 480 → capped at 300
    assert _compute_backoff_delay(5) == 300
    # attempt=10 → огромное число → capped at 300
    assert _compute_backoff_delay(10) == 300


# Тест: при attempt=0 (первая попытка) задержка = 30 секунд
def test_backoff_first_attempt():
    """Проверяем, что при первой попытке (attempt=0) задержка тоже 30 секунд."""
    # attempt=0 → 30 * 2^0 = 30 (max(0-1, 0) = 0)
    assert _compute_backoff_delay(0) == 30


# Тест: статус FAILED существует в enum
def test_failed_status_exists_in_enum():
    """Проверяем, что статус FAILED доступен в DisableTaskStatus."""
    assert DisableTaskStatus.FAILED == "FAILED"
    assert "FAILED" in [s.value for s in DisableTaskStatus]


@dataclass
class FakeSnapshot:
    """Фейковый снэпшот объявления."""

    fb_ad_id: str = "123456"
    delivery_status: str = "UNKNOWN"
    alert_state: AlertState = AlertState.CLAIMED


class FakeToggleHandle:
    """Фейковый переключатель объявления."""

    def __init__(self, *, aria_checked: str = "true", aria_label: str = "Выкл/вкл") -> None:
        self.aria_checked = aria_checked
        self.aria_label = aria_label
        self.click = AsyncMock()

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-checked":
            return self.aria_checked
        if name == "aria-label":
            return self.aria_label
        return None


class FakeToggleCell:
    """Фейковая ячейка таблицы с переключателем."""

    def __init__(self, toggle: object | None = None, fallback_toggle: object | None = None) -> None:
        self.toggle = toggle
        self.fallback_toggle = fallback_toggle

    async def query_selector(self, selector: str):
        if selector in ('[role="switch"]', '[role="switch"][aria-checked]'):
            return self.toggle
        if selector == '[aria-checked]:not([role="checkbox"])':
            return self.fallback_toggle
        return None


class FakeAdsPage:
    """Фейковая страница Ads Manager."""

    def __init__(self, cell: object | None) -> None:
        self.url = "https://adsmanager.facebook.com/adsmanager/manage/ads"
        self._cell = cell
        self.evaluate = AsyncMock()
        self.screenshot = AsyncMock()

    async def query_selector(self, selector: str):
        return self._cell


def _scalar_result(obj):
    """Создаёт мок результата scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    return result


# Тест: успешный клик без OFF оставляет объявление в ожидании подтверждения
@pytest.mark.asyncio
async def test_mark_succeeded_keeps_claimed_until_off_confirmed():
    """При SUCCEEDED без delivery_status=OFF снэпшот должен остаться в CLAIMED."""
    from run_disable_worker import mark_succeeded

    task = FakeDisableTask()
    snapshot = FakeSnapshot(delivery_status="UNKNOWN", alert_state=AlertState.CLAIMED)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[_scalar_result(task), _scalar_result(snapshot)])
    mock_factory = MagicMock(return_value=mock_session)

    with patch("run_disable_worker.get_session_factory", return_value=mock_factory):
        await mark_succeeded(task.id)

    assert task.status == DisableTaskStatus.SUCCEEDED
    assert snapshot.alert_state == AlertState.CLAIMED
    mock_session.commit.assert_awaited_once()


# Тест: успешный клик при уже OFF переводит снэпшот в DISABLED
@pytest.mark.asyncio
async def test_mark_succeeded_sets_disabled_when_delivery_off():
    """При delivery_status=OFF успешная задача должна пометить снэпшот как DISABLED."""
    from run_disable_worker import mark_succeeded

    task = FakeDisableTask()
    snapshot = FakeSnapshot(delivery_status="OFF", alert_state=AlertState.CLAIMED)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[_scalar_result(task), _scalar_result(snapshot)])
    mock_factory = MagicMock(return_value=mock_session)

    with patch("run_disable_worker.get_session_factory", return_value=mock_factory):
        await mark_succeeded(task.id)

    assert task.status == DisableTaskStatus.SUCCEEDED
    assert snapshot.alert_state == AlertState.DISABLED


# Тест: Vision-настройки сначала берутся из БД
@pytest.mark.asyncio
async def test_load_vision_settings_prefers_database():
    """Если в БД есть Vision-настройки, worker должен использовать их."""
    from run_disable_worker import _load_vision_settings

    row = SimpleNamespace(
        x_token_encrypted="encrypted-token",
        profile_id="profile-from-db",
        api_url="http://vision-db:3030",
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = MagicMock(return_value=mock_session)

    settings = SimpleNamespace(
        vision_x_token="env-token",
        vision_api_url="http://vision-env:3030",
        vision_profile_id="profile-from-env",
    )

    with (
        patch("run_disable_worker.get_session_factory", return_value=mock_factory),
        patch("run_disable_worker.get_settings", return_value=settings),
        patch("run_disable_worker.decrypt", return_value="decrypted-token"),
    ):
        token, api_url, profile_id = await _load_vision_settings()

    assert token == "decrypted-token"
    assert api_url == "http://vision-db:3030"
    assert profile_id == "profile-from-db"


# Тест: при пустой БД worker падает назад на .env
@pytest.mark.asyncio
async def test_load_vision_settings_falls_back_to_env():
    """Если Vision-настроек в БД нет, должны использоваться значения из .env."""
    from run_disable_worker import _load_vision_settings

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = MagicMock(return_value=mock_session)

    settings = SimpleNamespace(
        vision_x_token="env-token",
        vision_api_url="http://vision-env:3030",
        vision_profile_id="profile-from-env",
    )

    with (
        patch("run_disable_worker.get_session_factory", return_value=mock_factory),
        patch("run_disable_worker.get_settings", return_value=settings),
    ):
        token, api_url, profile_id = await _load_vision_settings()

    assert token == "env-token"
    assert api_url == "http://vision-env:3030"
    assert profile_id == "profile-from-env"


# Тест: disable worker отправляет нейтральное подтверждение, а не финальное "выключено"
@pytest.mark.asyncio
async def test_disable_worker_sends_pending_off_confirmation_to_telegram():
    """После клика воркер должен сообщать о ожидании OFF, а не о финальном выключении."""
    from apps.disable_worker.main import disable_worker_loop

    task = FakeDisableTask()
    call_count = 0

    async def claim_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        return None

    tg_client = AsyncMock()

    with patch("apps.disable_worker.main.TelegramBotClient", return_value=tg_client):
        loop_task = asyncio.create_task(
            disable_worker_loop(
                poll_interval_seconds=0,
                claim_next_task=claim_once,
                execute_disable=AsyncMock(
                    return_value=(True, "Переключатель дважды показал OFF в интерфейсе")
                ),
                mark_succeeded=AsyncMock(),
                mark_retrying=AsyncMock(),
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
            )
        )

        await asyncio.sleep(0.1)
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    tg_client.send_message.assert_awaited_once()
    sent_text = tg_client.send_message.await_args.kwargs["text"]
    assert "Клик по выключению выполнен" in sent_text
    assert "Ждём подтверждения статуса OFF" in sent_text
    assert "Объявление выключено" not in sent_text


# Тест: Playwright не использует двусмысленные fallback-контролы без точного switch
@pytest.mark.asyncio
async def test_execute_disable_requires_exact_switch():
    """Без точного переключателя функция должна завершаться ошибкой без клика."""
    from run_disable_worker import execute_disable_via_playwright

    page = FakeAdsPage(FakeToggleCell())
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()) as human_click,
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is False
    assert "Не найден точный switch-переключатель" in message
    human_click.assert_not_awaited()


# Тест: aria-checked без role=switch не считается валидным переключателем
@pytest.mark.asyncio
async def test_execute_disable_ignores_non_switch_aria_checked_controls():
    """Fallback-контролы с aria-checked без role=switch должны игнорироваться."""
    from run_disable_worker import execute_disable_via_playwright

    page = FakeAdsPage(FakeToggleCell(toggle=None, fallback_toggle=FakeToggleHandle()))
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()) as human_click,
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is False
    assert "Не найден точный switch-переключатель" in message
    human_click.assert_not_awaited()


# Тест: Playwright подтверждает успех только после двух последовательных false
@pytest.mark.asyncio
async def test_execute_disable_waits_for_two_false_reads():
    """Успех возможен только когда aria-checked дважды подряд становится false."""
    from run_disable_worker import execute_disable_via_playwright

    toggle = FakeToggleHandle(aria_checked="true")
    page = FakeAdsPage(FakeToggleCell(toggle=toggle))
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()) as human_click,
        patch("run_disable_worker._confirm_dialog_if_present", new=AsyncMock(return_value=False)),
        patch(
            "run_disable_worker._get_aria_checked_via_js",
            new=AsyncMock(side_effect=["false", "false"]),
        ),
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is True
    assert "дважды подтвердил состояние OFF" in message
    human_click.assert_awaited_once_with(page, toggle, double_check_pause=True)


# Тест: расширенное окно подтверждения ловит позднее обновление aria-checked
@pytest.mark.asyncio
async def test_execute_disable_allows_late_off_confirmation_inside_extended_window():
    """Поздний переход в false должен успевать подтверждаться без лишнего RETRYING."""
    from run_disable_worker import execute_disable_via_playwright

    toggle = FakeToggleHandle(aria_checked="true")
    page = FakeAdsPage(FakeToggleCell(toggle=toggle))
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()),
        patch("run_disable_worker._confirm_dialog_if_present", new=AsyncMock(return_value=False)),
        patch(
            "run_disable_worker._get_aria_checked_via_js",
            new=AsyncMock(side_effect=["true", "true", "true", "true", "false", "false"]),
        ),
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is True
    assert "дважды подтвердил состояние OFF" in message


# Тест: после расширенной проверки ошибка должна явно говорить, что OFF не подтвердился
@pytest.mark.asyncio
async def test_execute_disable_reports_extended_confirmation_failure():
    """Если OFF так и не появился, ошибка должна отражать расширенную проверку интерфейса."""
    from run_disable_worker import execute_disable_via_playwright

    toggle = FakeToggleHandle(aria_checked="true")
    page = FakeAdsPage(FakeToggleCell(toggle=toggle))
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()),
        patch("run_disable_worker._confirm_dialog_if_present", new=AsyncMock(return_value=False)),
        patch(
            "run_disable_worker._get_aria_checked_via_js",
            new=AsyncMock(side_effect=["true"] * 9),
        ),
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is False
    assert "расширенной проверки" in message
    assert "сек" in message
