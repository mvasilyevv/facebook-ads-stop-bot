# -*- coding: utf-8 -*-
"""Тесты для disable worker: retry-логика, подтверждение OFF и backoff."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
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

    async def query_selector_all(self, selector: str):
        return []


# Фейковая кнопка для диалога подтверждения.
class FakeDialogButton:
    """Фейковая кнопка подтверждения в диалоге."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.click = AsyncMock()

    async def inner_text(self) -> str:
        return self._text


def _scalar_result(obj):
    """Создаёт мок результата scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    return result


# Тест: подтверждение диалога должно идти через human_click, а не через прямой click.
@pytest.mark.asyncio
async def test_confirm_dialog_uses_human_click():
    """Кнопка подтверждения должна нажиматься через humanizer."""
    from run_disable_worker import _confirm_dialog_if_present

    button = FakeDialogButton("Опубликовать")
    page = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[button])

    with (
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker.human_click", new=AsyncMock()) as human_click,
    ):
        confirmed = await _confirm_dialog_if_present(page)

    assert confirmed is True
    human_click.assert_awaited_once_with(page, button, double_check_pause=False)
    button.click.assert_not_called()


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


# Тест: отменённая архивная задача не должна возвращаться в RETRYING
@pytest.mark.asyncio
async def test_mark_retrying_ignores_cancelled_task():
    """Если задача уже отменена как архивная, worker не должен оживлять её повтором."""
    from run_disable_worker import mark_retrying

    task = FakeDisableTask(status=DisableTaskStatus.CANCELLED)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalar_result(task))
    mock_factory = MagicMock(return_value=mock_session)

    with patch("run_disable_worker.get_session_factory", return_value=mock_factory):
        await mark_retrying(task.id, "Ложное срабатывание", datetime.now(UTC))

    assert task.status == DisableTaskStatus.CANCELLED
    mock_session.commit.assert_not_awaited()


# Тест: таймаут отключения браузера в cleanup не должен подвешивать recovery-цикл.
@pytest.mark.asyncio
async def test_close_disable_runtime_resources_tolerates_disconnect_timeout():
    """При зависшем disconnect cleanup должен завершаться по таймауту и идти дальше."""
    from run_disable_worker import _close_disable_runtime_resources

    async def hang_disconnect():
        await asyncio.sleep(3600)

    manager = SimpleNamespace(disconnect=hang_disconnect)
    vision = SimpleNamespace(close=AsyncMock())

    with patch("run_disable_worker.DISABLE_MANAGER_DISCONNECT_TIMEOUT_SECONDS", 0.01):
        await _close_disable_runtime_resources(manager, vision)

    vision.close.assert_awaited_once_with()


# Тест: таймаут закрытия Vision-клиента не должен подвешивать recovery-цикл.
@pytest.mark.asyncio
async def test_close_disable_runtime_resources_tolerates_vision_close_timeout():
    """При зависшем Vision.close cleanup должен завершаться по таймауту и идти дальше."""
    from run_disable_worker import _close_disable_runtime_resources

    manager = SimpleNamespace(disconnect=AsyncMock())

    async def hang_close():
        await asyncio.sleep(3600)

    vision = SimpleNamespace(close=hang_close)

    with patch("run_disable_worker.DISABLE_VISION_CLOSE_TIMEOUT_SECONDS", 0.01):
        await _close_disable_runtime_resources(manager, vision)

    manager.disconnect.assert_awaited_once_with()


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


# Тест: disable worker больше не шлёт TG напрямую, а использует общий lifecycle-колбэк.
@pytest.mark.asyncio
async def test_disable_worker_uses_completion_callback_instead_of_direct_telegram_send():
    """После клика воркер должен вызывать lifecycle-колбэк и не слать TG напрямую."""
    from apps.disable_worker.main import disable_worker_loop

    task = FakeDisableTask()
    call_count = 0

    async def claim_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        return None

    send_completion_callback = AsyncMock()

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
            send_completion_callback=send_completion_callback,
        )
    )

    await asyncio.sleep(0.1)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    send_completion_callback.assert_awaited_once_with(
        task,
        True,
        "Переключатель дважды показал OFF в интерфейсе",
    )


# Тест: пакетный режим должен обрабатывать несколько задач за один проход цикла.
@pytest.mark.asyncio
async def test_disable_worker_loop_processes_task_batch():
    """Если воркер взял пачку задач, он должен обработать их без возврата в одиночный режим."""
    from apps.disable_worker.main import disable_worker_loop

    task_one = FakeDisableTask(id="task-001", fb_ad_id="111111")
    task_two = FakeDisableTask(id="task-002", fb_ad_id="222222", attempt_count=2)
    batch_calls = 0

    async def claim_batch(limit: int):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls == 1:
            assert limit == 10
            return [task_one, task_two]
        return []

    mark_succeeded = AsyncMock()
    mark_retrying = AsyncMock()
    mark_failed = AsyncMock()
    execute_disable_batch = AsyncMock(
        return_value={
            task_one.id: (True, "Первое объявление отключено"),
            task_two.id: (False, "Второе объявление не найдено в таблице"),
        }
    )

    loop_task = asyncio.create_task(
        disable_worker_loop(
            poll_interval_seconds=0,
            claim_next_task=AsyncMock(return_value=None),
            execute_disable=AsyncMock(),
            claim_task_batch=claim_batch,
            execute_disable_batch=execute_disable_batch,
            batch_size=10,
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            mark_failed=mark_failed,
        )
    )

    await asyncio.sleep(0.1)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    execute_disable_batch.assert_awaited_once_with([task_one, task_two])
    mark_succeeded.assert_awaited_once_with(task_one.id)
    mark_retrying.assert_awaited_once()
    mark_failed.assert_not_called()


# Тест: зависшая одиночная браузерная операция должна переводить задачу в RETRYING и ронять цикл для переподключения.
@pytest.mark.asyncio
async def test_disable_worker_loop_retries_and_restarts_on_single_task_timeout():
    """Таймаут одной задачи должен вернуть её в очередь и пробросить фатальную ошибку вверх."""
    from apps.disable_worker.main import BrowserOperationTimeoutError, disable_worker_loop

    task = FakeDisableTask(id="task-timeout", fb_ad_id="999001")
    call_count = 0

    async def claim_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        return None

    async def hang_disable(_fb_ad_id: str):
        await asyncio.sleep(3600)
        return False, "не дойдём сюда"

    mark_succeeded = AsyncMock()
    mark_retrying = AsyncMock()
    mark_failed = AsyncMock()

    with patch("apps.disable_worker.main.DISABLE_BROWSER_TASK_TIMEOUT_SECONDS", 0.01):
        with pytest.raises(BrowserOperationTimeoutError):
            await disable_worker_loop(
                poll_interval_seconds=0,
                claim_next_task=claim_once,
                execute_disable=hang_disable,
                mark_succeeded=mark_succeeded,
                mark_retrying=mark_retrying,
                mark_failed=mark_failed,
            )

    mark_succeeded.assert_not_called()
    mark_failed.assert_not_called()
    mark_retrying.assert_awaited_once()
    assert "превысила таймаут" in mark_retrying.await_args.args[1]


# Тест: зависшая пакетная браузерная операция должна вернуть всю пачку в RETRYING и инициировать переподключение.
@pytest.mark.asyncio
async def test_disable_worker_loop_retries_batch_on_browser_timeout():
    """Таймаут пакетного прохода должен пометить все задачи на повтор и завершить цикл ошибкой."""
    from apps.disable_worker.main import BrowserOperationTimeoutError, disable_worker_loop

    task_one = FakeDisableTask(id="task-batch-1", fb_ad_id="999101")
    task_two = FakeDisableTask(id="task-batch-2", fb_ad_id="999102", attempt_count=2)
    batch_calls = 0

    async def claim_batch(limit: int):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls == 1:
            assert limit == 10
            return [task_one, task_two]
        return []

    async def hang_batch(_tasks):
        await asyncio.sleep(3600)
        return {}

    mark_succeeded = AsyncMock()
    mark_retrying = AsyncMock()
    mark_failed = AsyncMock()

    with patch("apps.disable_worker.main.DISABLE_BROWSER_BATCH_TIMEOUT_SECONDS", 0.01):
        with pytest.raises(BrowserOperationTimeoutError):
            await disable_worker_loop(
                poll_interval_seconds=0,
                claim_next_task=AsyncMock(return_value=None),
                execute_disable=AsyncMock(),
                claim_task_batch=claim_batch,
                execute_disable_batch=hang_batch,
                batch_size=10,
                mark_succeeded=mark_succeeded,
                mark_retrying=mark_retrying,
                mark_failed=mark_failed,
            )

    mark_succeeded.assert_not_called()
    mark_failed.assert_not_called()
    assert mark_retrying.await_count == 2
    assert "пачки из 2 задач" in mark_retrying.await_args_list[0].args[1]


# Тест: при сбое humanizer disable worker не должен падать в обычный click.
@pytest.mark.asyncio
async def test_execute_disable_does_not_fallback_to_direct_click():
    """Если human_click ломается, обычный click использоваться не должен."""
    from run_disable_worker import execute_disable_via_playwright

    toggle = FakeToggleHandle(aria_checked="true")
    page = FakeAdsPage(FakeToggleCell(toggle=toggle))
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch(
            "run_disable_worker.human_click",
            new=AsyncMock(side_effect=RuntimeError("сбой humanizer")),
        ),
        patch("run_disable_worker._confirm_dialog_if_present", new=AsyncMock(return_value=False)),
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is False
    assert "humanizer" in message
    toggle.click.assert_not_awaited()


# Тест: пакетный поиск должен идти сверху вниз и находить объявления на разных шагах прокрутки.
@pytest.mark.asyncio
async def test_execute_disable_batch_scans_table_top_to_bottom():
    """Пакетный обход должен найти несколько объявлений за один проход сверху вниз."""
    from run_disable_worker import execute_disable_batch_via_playwright

    task_one = FakeDisableTask(id="task-001", fb_ad_id="111111")
    task_two = FakeDisableTask(id="task-002", fb_ad_id="222222")
    page = FakeAdsPage(FakeToggleCell(toggle=FakeToggleHandle(aria_checked="true")))
    manager = MagicMock()

    with (
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch(
            "run_disable_worker._resolve_ads_manager_page", new=AsyncMock(return_value=(page, None))
        ),
        patch("run_disable_worker._reset_ads_table_scroll", new=AsyncMock()) as reset_scroll_mock,
        patch(
            "run_disable_worker.get_visible_ads_table_row_ids",
            new=AsyncMock(side_effect=[["111111"], ["222222"]]),
        ),
        patch(
            "run_disable_worker.get_ads_table_scroll_metrics",
            new=AsyncMock(
                return_value={
                    "found": True,
                    "scroll_top": 0.0,
                    "max_scroll_top": 600.0,
                    "at_bottom": False,
                    "moved": False,
                }
            ),
        ),
        patch(
            "run_disable_worker.scroll_ads_table_down",
            new=AsyncMock(
                return_value={
                    "found": True,
                    "scroll_top": 220.0,
                    "max_scroll_top": 600.0,
                    "at_bottom": False,
                    "moved": True,
                }
            ),
        ) as scroll_mock,
        patch(
            "run_disable_worker._find_toggle_cell_in_dom", new=AsyncMock(return_value=page._cell)
        ),
        patch(
            "run_disable_worker._execute_disable_on_page",
            new=AsyncMock(side_effect=[(True, "Отключено 1"), (True, "Отключено 2")]),
        ) as execute_mock,
    ):
        results = await execute_disable_batch_via_playwright(manager, [task_one, task_two])

    assert results == {
        task_one.id: (True, "Отключено 1"),
        task_two.id: (True, "Отключено 2"),
    }
    reset_scroll_mock.assert_awaited_once_with(page)
    scroll_mock.assert_awaited_once()
    assert execute_mock.await_count == 2


# Тест: если таблица уже внизу и дальше скроллить нельзя, worker должен вернуться к поиску сверху.
@pytest.mark.asyncio
async def test_execute_disable_batch_falls_back_to_legacy_search_from_bottom():
    """При упоре в низ таблицы остаток пачки должен добиваться старым поиском сверху."""
    from run_disable_worker import execute_disable_batch_via_playwright

    task = FakeDisableTask(id="task-legacy", fb_ad_id="333333")
    page = FakeAdsPage(FakeToggleCell(toggle=FakeToggleHandle(aria_checked="true")))
    manager = MagicMock()

    with (
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch(
            "run_disable_worker._resolve_ads_manager_page", new=AsyncMock(return_value=(page, None))
        ),
        patch("run_disable_worker._reset_ads_table_scroll", new=AsyncMock()) as reset_scroll_mock,
        patch("run_disable_worker.get_visible_ads_table_row_ids", new=AsyncMock(return_value=[])),
        patch(
            "run_disable_worker.get_ads_table_scroll_metrics",
            new=AsyncMock(
                return_value={
                    "found": True,
                    "scroll_top": 600.0,
                    "max_scroll_top": 600.0,
                    "at_bottom": True,
                    "moved": False,
                }
            ),
        ),
        patch("run_disable_worker.scroll_ads_table_down", new=AsyncMock()) as scroll_mock,
        patch("run_disable_worker._find_toggle_cell_in_dom", new=AsyncMock(return_value=None)),
        patch(
            "run_disable_worker._execute_disable_on_page",
            new=AsyncMock(return_value=(True, "Отключено через fallback")),
        ) as execute_mock,
    ):
        results = await execute_disable_batch_via_playwright(manager, [task])

    assert results == {task.id: (True, "Отключено через fallback")}
    assert reset_scroll_mock.await_count == 2
    scroll_mock.assert_not_awaited()
    execute_mock.assert_awaited_once_with(
        page,
        task.fb_ad_id,
        reset_table_before_search=True,
        allow_scroll_search=True,
    )


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


# Тест: перед поиском worker должен сбрасывать внутренний скролл таблицы Ads Manager.
@pytest.mark.asyncio
async def test_execute_disable_resets_ads_table_scroll_before_search():
    """Если строка не видна, worker должен вернуть таблицу к началу и только потом искать."""
    from run_disable_worker import execute_disable_via_playwright

    toggle = FakeToggleHandle(aria_checked="true")
    cell = FakeToggleCell(toggle=toggle)
    page = FakeAdsPage(None)
    manager = MagicMock()

    with (
        patch("run_disable_worker._find_ads_manager_page", return_value=page),
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker._reset_ads_table_scroll", new=AsyncMock()) as reset_scroll_mock,
        patch(
            "run_disable_worker._find_toggle_cell_in_dom",
            new=AsyncMock(side_effect=[None, cell]),
        ),
        patch(
            "run_disable_worker.get_ads_table_scroll_metrics",
            new=AsyncMock(
                return_value={
                    "found": True,
                    "scroll_top": 0.0,
                    "max_scroll_top": 1200.0,
                    "at_bottom": False,
                    "moved": False,
                }
            ),
        ),
        patch(
            "run_disable_worker.scroll_ads_table_down",
            new=AsyncMock(
                return_value={
                    "found": True,
                    "scroll_top": 220.0,
                    "max_scroll_top": 1200.0,
                    "at_bottom": False,
                    "moved": True,
                }
            ),
        ),
        patch("run_disable_worker.human_click", new=AsyncMock()),
        patch("run_disable_worker._confirm_dialog_if_present", new=AsyncMock(return_value=False)),
        patch(
            "run_disable_worker._wait_for_disable_confirmation",
            new=AsyncMock(return_value=(True, "Объявление выключено")),
        ),
    ):
        success, message = await execute_disable_via_playwright(manager, "123456")

    assert success is True
    assert "Объявление выключено" in message
    reset_scroll_mock.assert_awaited_once_with(page)


# Тест: одиночный поиск должен идти до позднего появления строки, а не обрываться по раннему лимиту шагов.
@pytest.mark.asyncio
async def test_find_toggle_cell_with_table_scan_reaches_late_row():
    """Поиск по одной задаче должен пролистывать таблицу до появления нужной строки."""
    from run_disable_worker import _find_toggle_cell_with_table_scan

    page = FakeAdsPage(None)
    cell = FakeToggleCell(toggle=FakeToggleHandle(aria_checked="true"))

    with (
        patch("run_disable_worker.asyncio.sleep", new=AsyncMock()),
        patch("run_disable_worker._reset_ads_table_scroll", new=AsyncMock()) as reset_scroll_mock,
        patch(
            "run_disable_worker._find_toggle_cell_in_dom",
            new=AsyncMock(side_effect=[None, None, cell]),
        ),
        patch(
            "run_disable_worker.get_ads_table_scroll_metrics",
            new=AsyncMock(
                side_effect=[
                    {
                        "found": True,
                        "scroll_top": 0.0,
                        "max_scroll_top": 2200.0,
                        "at_bottom": False,
                        "moved": False,
                    },
                    {
                        "found": True,
                        "scroll_top": 220.0,
                        "max_scroll_top": 2200.0,
                        "at_bottom": False,
                        "moved": False,
                    },
                ]
            ),
        ),
        patch(
            "run_disable_worker.scroll_ads_table_down",
            new=AsyncMock(
                side_effect=[
                    {
                        "found": True,
                        "scroll_top": 220.0,
                        "max_scroll_top": 2200.0,
                        "at_bottom": False,
                        "moved": True,
                    },
                    {
                        "found": True,
                        "scroll_top": 440.0,
                        "max_scroll_top": 2200.0,
                        "at_bottom": False,
                        "moved": True,
                    },
                ]
            ),
        ) as scroll_mock,
        patch("run_disable_worker.human_scroll_to_find", new=AsyncMock()) as legacy_scroll_mock,
    ):
        found = await _find_toggle_cell_with_table_scan(page, "123456", reset_to_top=True)

    assert found is cell
    reset_scroll_mock.assert_awaited_once_with(page)
    assert scroll_mock.await_count == 2
    legacy_scroll_mock.assert_not_awaited()


# Тест: жёсткий сброс таблицы должен дополнительно прокручивать колесо вверх над областью таблицы.
@pytest.mark.asyncio
async def test_reset_ads_table_scroll_rewinds_table_with_upward_wheel():
    """Disable worker должен уводить таблицу к началу тем же способом, что и observer."""
    from run_disable_worker import _reset_ads_table_scroll

    page = AsyncMock()
    page.viewport_size = {"width": 1200, "height": 800}

    with (
        patch(
            "run_disable_worker.get_ads_table_scroll_anchor",
            new=AsyncMock(return_value=(600.0, 400.0)),
        ),
        patch("run_disable_worker.human_move", new=AsyncMock()) as human_move_mock,
        patch(
            "run_disable_worker.reset_ads_table_scroll", new=AsyncMock(return_value=2)
        ) as reset_mock,
        patch("run_disable_worker.human_wheel_scroll", new=AsyncMock()) as human_wheel_scroll_mock,
    ):
        await _reset_ads_table_scroll(page)

    human_move_mock.assert_awaited_once_with(page, 600.0, 400.0)
    reset_mock.assert_awaited_once_with(page)
    assert human_wheel_scroll_mock.await_count == 4
    for call in human_wheel_scroll_mock.await_args_list:
        assert call.args == (page, -1200)
        assert call.kwargs["anchor"] == (600.0, 400.0)
        assert call.kwargs["move_before"] is False


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
