# -*- coding: utf-8 -*-
"""Unit (#211): исход отказа браузера и политика его повтора — разные вопросы.

browser-agent отвергает операцию сам, до отправки в Meta, и называет причину.
Исход у всей семьи один и не обсуждается: ``REJECTED`` — отправки не было,
объектов не создано, сверять оператору нечего.

Политика повтора у той же семьи разная и задаётся кодом причины:

- истёкший грант, недоступный сервис выдачи разрешений — повтор осмыслен,
  задача возвращается в очередь;
- неверная подпись, чужой кабинет, неавторизованный вызывающий, сломанная
  семантика Graph-запроса, кабинет не числом — повтором той же задачи не
  лечится, и вечный requeue money-задачи прячет поломку вызывающего вместо
  того, чтобы её показать.

Проверяется наблюдаемое поведение воркеров обеих полос: чем закончилась задача
(финализирована или вернулась в очередь) и увидит ли оператор причину.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import core.campaign_builder.execute as campaign_execute
from core.meta_api.errors import (
    BROWSER_OPERATION_PERMANENT_REJECTIONS,
    BROWSER_OPERATION_REJECTION_REASONS,
    BrowserOperationRejectedError,
    BrowserReadinessRejectedError,
    unretryable_browser_rejection,
)
from core.tasks.action_reason import (
    browser_rejection_not_retryable_reason,
    campaign_operator_reason,
    operator_reason_from_result,
)
from core.tasks.queue import Task

# Причина, которую повтор не лечит: подпись разрешения не сошлась.
_UNRETRYABLE_REASON = "capability_signature_invalid"
# Причина той же семьи, которую повтор лечит: грант истёк, следующий будет свежим.
_RETRYABLE_REASON = "capability_expired"


def _rejection(reason_code: str) -> BrowserOperationRejectedError:
    """Отказ ровно в той форме, в какой его строит клиент Marketing API.

    Текст берётся из таблицы причин, но код, которого в ней нет, собирается без
    текста намеренно: тест про политику повтора обязан падать на поведении
    задачи, а не на KeyError при сборке исключения.
    """
    named = BROWSER_OPERATION_REJECTION_REASONS.get(reason_code, "причина не названа")
    return BrowserOperationRejectedError(
        f"browser-agent отверг операцию до отправки в Meta: {named}",
        reason_code=reason_code,
        endpoint="/act_456/ads",
    )


# Каждая неисправимая причина обязана иметь человеческий текст: иначе оператор
# получит задачу, которая больше не повторится, и «Причина не записана».
def test_every_unretryable_reason_has_operator_wording() -> None:
    assert BROWSER_OPERATION_PERMANENT_REJECTIONS
    assert BROWSER_OPERATION_PERMANENT_REJECTIONS <= set(BROWSER_OPERATION_REJECTION_REASONS)


# ============ разбор корзины: имя на каждую природу отказа (#226) ============

# Грант пришёл без пригодного срока действия. Единственный путь, который живьём
# доходил до общего имени `capability_invalid`: поток UploadVideo связывает срок
# с первого чанка — раньше, чем подпись вообще может быть проверена, потому что
# для подписи нужен дайджест всего файла. Свойство самого запроса, не окружения.
_GRANT_WITHOUT_DEADLINE = "capability_expiry_missing"
# Остаток корзины: текст про разрешение на операцию, который ни один именованный
# предикат не узнал. Причина не установлена — и так и называется.
_UNNAMED_REFUSAL = "capability_reason_unknown"


def test_the_bucket_is_split_and_each_half_states_its_retry_verdict() -> None:
    # Импорт локальный: тесты поведения ниже обязаны падать на поведении задачи,
    # а не на том, что структуры таблицы ещё нет.
    from core.meta_api.errors import (
        BROWSER_OPERATION_REJECTION_TABLE,
        BrowserRejectionRetry,
    )

    # Общего имени на отказы разной природы больше нет.
    assert "capability_invalid" not in BROWSER_OPERATION_REJECTION_TABLE
    assert (
        BROWSER_OPERATION_REJECTION_TABLE[_GRANT_WITHOUT_DEADLINE].retry
        is BrowserRejectionRetry.DOES_NOT_HELP
    )
    # Незнание остаётся незнанием: повтор разрешён, вывод про недействительное
    # разрешение не делается.
    assert (
        BROWSER_OPERATION_REJECTION_TABLE[_UNNAMED_REFUSAL].retry is BrowserRejectionRetry.UNKNOWN
    )
    assert _UNNAMED_REFUSAL not in BROWSER_OPERATION_PERMANENT_REJECTIONS


def test_a_reason_cannot_exist_without_stating_whether_a_retry_helps() -> None:
    """Вердикт про повтор — обязательное поле, а не строка во втором наборе.

    Раньше «повтор помогает» выражалось ОТСУТСТВИЕМ кода во втором наборе, то
    есть не выражалось вовсе: код, добавленный в словарь причин и забытый в
    наборе неисправимых, молча получал вечный повтор money-задачи — вердикт,
    которого никто не выносил.
    """
    from core.meta_api.errors import (
        BROWSER_OPERATION_REJECTION_TABLE,
        BrowserRejectionReason,
        BrowserRejectionRetry,
    )

    with pytest.raises(TypeError):
        BrowserRejectionReason("причина без вердикта про повтор")  # type: ignore[call-arg]

    for code, reason in BROWSER_OPERATION_REJECTION_TABLE.items():
        assert isinstance(reason.retry, BrowserRejectionRetry), code
        assert reason.text
        # Набор неисправимых — производная от вердикта, а не второй список,
        # который совпадает с первым по договорённости.
        assert (code in BROWSER_OPERATION_PERMANENT_REJECTIONS) is (
            reason.retry is BrowserRejectionRetry.DOES_NOT_HELP
        ), code


# ====================== money-полоса: apps/meta_api_worker ======================


def _money_task(**over) -> Task:
    now = datetime.now(UTC)
    base = dict(
        id=211,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key="meta:pause_ad:211",
        payload={"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "456"},
        attempt_count=0,
        max_attempts=72,
        requested_by="bot_auto_stop",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=100,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000211"),
        lease_token=3,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )
    base.update(over)
    return Task(**base)


@pytest.fixture
def money_worker(monkeypatch):
    """Воркер доходит до внешнего вызова; все финализаторы под наблюдением."""
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    spies = SimpleNamespace(
        failed=AsyncMock(return_value=True),
        requeue=AsyncMock(return_value=True),
        requeue_pre_send=AsyncMock(return_value="retrying"),
        release_readiness=AsyncMock(return_value="retrying"),
        alert=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(meta, "mark_task_failed", spies.failed)
    monkeypatch.setattr(meta, "requeue_task", spies.requeue)
    monkeypatch.setattr(meta, "requeue_task_proven_not_committed", spies.requeue_pre_send)
    monkeypatch.setattr(
        meta,
        "release_task_after_browser_readiness_rejection",
        spies.release_readiness,
    )
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spies.alert)
    return spies


@pytest.mark.asyncio
async def test_money_task_with_unretryable_rejection_is_finalized_rejected(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNRETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    # Повторов больше нет — ни обычного, ни доказанного pre-send.
    money_worker.requeue.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()
    money_worker.failed.assert_awaited_once()
    result = money_worker.failed.await_args.kwargs["result"]
    # Исход не изменился: отправки не было, сверять нечего.
    assert result["outcome"] == "REJECTED"
    assert result.get("reconcile_required") is not True
    assert result.get("manual_review_required") is not True
    # Причина доезжает до карточки оператора, а не только до лога.
    assert operator_reason_from_result(result) is not None


# Money-сигнал не исчезает вместе с повторами: команда «выключить» не дошла до
# кабинета и больше не дойдёт — объявление продолжает тратить бюджет.
@pytest.mark.asyncio
async def test_money_task_with_unretryable_rejection_still_signals_undelivered_stop(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNRETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.alert.assert_awaited_once()
    assert money_worker.alert.await_args.kwargs["fb_ad_id"] == "123"


@pytest.mark.asyncio
async def test_money_task_with_retryable_rejection_returns_to_queue(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_RETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.requeue_pre_send.assert_awaited_once()
    money_worker.failed.assert_not_awaited()


# Первая половина разобранной корзины. Повтор соберёт такой же грант без срока
# действия, поэтому задача закрывается сразу, а не крутится до исчерпания
# попыток, пряча поломку вызывающего под видом недоступного канала.
@pytest.mark.asyncio
async def test_grant_without_a_deadline_finalizes_the_money_task_without_retries(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_GRANT_WITHOUT_DEADLINE)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.requeue.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()
    money_worker.failed.assert_awaited_once()
    result = money_worker.failed.await_args.kwargs["result"]
    # Исход не пересматривается вместе с политикой повтора: отправки не было.
    assert result["outcome"] == "REJECTED"
    assert result["pre_dispatch"] is True
    assert result.get("reconcile_required") is not True
    assert result.get("manual_review_required") is not True
    # Причина названа словами, а не кодом: без своей строки в закрытом словаре
    # оператор прочитал бы «Причина не записана» у задачи, которая больше не
    # повторится. Машинный код в карточку не уезжает.
    operator_text = operator_reason_from_result(result)
    assert operator_text is not None
    assert "разрешение на операцию пришло без срока действия" in operator_text
    assert "Повтор той же задачи не поможет" in operator_text
    assert _GRANT_WITHOUT_DEADLINE not in operator_text


# Вторая половина: причину назвать нечем. Повтор разрешён — но оператор читает
# именно это, а не вывод «разрешение недействительно», которого никто не делал.
@pytest.mark.asyncio
async def test_unnamed_refusal_returns_to_queue_and_says_the_cause_is_unestablished(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNNAMED_REFUSAL)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.requeue_pre_send.assert_awaited_once()
    money_worker.failed.assert_not_awaited()

    operator_text = campaign_operator_reason(rejection_reason_code=_UNNAMED_REFUSAL)
    assert operator_text is not None
    assert "причина отказа не установлена" in operator_text


# Отказ готовности канала остаётся отдельной семьёй: задача возвращается в
# очередь, не сжигая попытку. Политика повтора по коду причины его не касается.
@pytest.mark.asyncio
async def test_money_task_with_readiness_rejection_is_released_without_burn(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=BrowserReadinessRejectedError("channel is not ready")),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.release_readiness.assert_awaited_once()
    money_worker.failed.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()


# Порядок вопросов, а не совпадение gRPC-статусов: сегодня отказ готовности и
# неисправимый отказ операции приходят на разных статусах и не пересекаются.
# Стоит одному завернуть другой в ``__cause__`` — и задача, которую полагается
# вернуть в очередь без сгорания попытки, финализировалась бы отказом.
@pytest.mark.asyncio
async def test_money_readiness_rejection_wins_over_unretryable_cause(
    monkeypatch, money_worker
) -> None:
    readiness = BrowserReadinessRejectedError("channel is not ready")
    readiness.__cause__ = _rejection(_UNRETRYABLE_REASON)
    # Ровно та коллизия, которой сегодня не бывает: обе семьи узнают себя в
    # одной ошибке, и решает порядок вопросов, а не то, что коды разошлись.
    assert unretryable_browser_rejection(readiness) is not None
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=readiness))

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.release_readiness.assert_awaited_once()
    money_worker.failed.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()


# Самый дорогой маршрут через новую ветку: необратимая мутация создаёт объекты в
# кабинете, и отсутствие подтверждения по умолчанию становится UNKNOWN с ручной
# сверкой. Но отказ ДО отправки доказан браузером: дубля нет и сверять нечего,
# поэтому исход обязан остаться REJECTED, а не уехать в _fail_irreversible.
@pytest.mark.asyncio
async def test_money_irreversible_duplicate_with_unretryable_rejection_stays_rejected(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "authorize_duplicate_execution_boundary",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNRETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(
            id=213,
            idempotency_key="meta:duplicate_adset_structure:213",
            payload={
                "mutation_kind": "duplicate_adset_structure",
                "target_id": "555",
                "ad_account_id": "456",
            },
            lane="bulk",
            requested_by="operator",
        ),
        client=AsyncMock(),
    )

    money_worker.failed.assert_awaited_once()
    result = money_worker.failed.await_args.kwargs["result"]
    assert result["outcome"] == "REJECTED"
    # Признаки пути необратимого UNKNOWN: их здесь быть не должно — оператора
    # нельзя звать сверять кабинет, в который ничего не отправляли.
    assert result.get("reconcile_required") is not True
    assert result.get("manual_review_required") is not True
    assert operator_reason_from_result(result) is not None


# ====================== bulk-полоса: apps/campaign_creator_worker ======================


class _UnitControl:
    def __init__(self, **kwargs) -> None:
        self.external_started = False

    async def check(self) -> None:
        return None

    async def begin_external(self, _operation: str) -> None:
        self.external_started = True


def _creator_task() -> Task:
    now = datetime.now(UTC)
    return Task(
        id=212,
        task_type="campaign_create",
        status="running",
        idempotency_key="campaign:run-211",
        payload={"run_id": "run-211"},
        attempt_count=0,
        max_attempts=5,
        requested_by="operator",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000212"),
        lease_token=4,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


async def _run_creator_until_rejection(
    monkeypatch,
    *,
    reason_code: str,
    external_started: bool = False,
):
    """Гоняет залив до отказа браузера, завёрнутого ровно как в бою."""
    import apps.campaign_creator_worker.main as worker

    async def _execute(*_args, **_kwargs):
        # Обёртку строит production-код: воркер видит CampaignExecutionError,
        # а названная причина живёт в цепочке __cause__.
        campaign_execute._raise_for_failure(  # noqa: SLF001
            {"campaigns": [], "adsets": [], "creatives": [], "ads": []},
            _rejection(reason_code),
            failed_step="creating_campaign",
        )

    async def _direct(_control, operation_factory):
        return await operation_factory()

    cfg = SimpleNamespace(account=SimpleNamespace(act_id="act_456"))
    monkeypatch.setattr(worker, "CreatorTaskControl", _UnitControl)
    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: cfg)
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    spies = SimpleNamespace(
        failed=AsyncMock(return_value=True),
        retry=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(worker, "finalize_run_failed", spies.failed)
    monkeypatch.setattr(worker, "requeue_for_retry", spies.retry)

    control = _UnitControl()
    # begin_external выставляется перед КАЖДЫМ внешним вызовом, начиная с
    # загрузки креативов, поэтому флаг сам по себе ничего не говорит про исход
    # того вызова, на котором залив упал.
    control.external_started = external_started
    await worker._execute_run(  # noqa: SLF001
        object(),
        _creator_task(),
        run_id="run-211",
        config={},
        client=AsyncMock(),
        uploader=AsyncMock(),
        control=control,
    )
    return spies


@pytest.mark.asyncio
async def test_creator_task_with_unretryable_rejection_is_finalized_rejected(
    monkeypatch,
) -> None:
    spies = await _run_creator_until_rejection(monkeypatch, reason_code=_UNRETRYABLE_REASON)

    spies.retry.assert_not_awaited()
    spies.failed.assert_awaited_once()
    result = spies.failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "REJECTED"
    assert result.get("reconcile_required") is not True
    assert operator_reason_from_result(result) is not None


@pytest.mark.asyncio
async def test_creator_task_with_retryable_rejection_returns_to_queue(monkeypatch) -> None:
    spies = await _run_creator_until_rejection(monkeypatch, reason_code=_RETRYABLE_REASON)

    spies.retry.assert_awaited_once()
    spies.failed.assert_not_awaited()


# Полоса, где грант без срока действия рождается живьём: заливом правит поток
# UploadVideo, и срок связывается с первого чанка. Повтор соберёт такой же
# грант, поэтому залив закрывается отказом, а не уходит на новый круг.
@pytest.mark.asyncio
async def test_creator_task_with_a_grant_without_deadline_is_finalized_rejected(
    monkeypatch,
) -> None:
    spies = await _run_creator_until_rejection(monkeypatch, reason_code=_GRANT_WITHOUT_DEADLINE)

    spies.retry.assert_not_awaited()
    spies.failed.assert_awaited_once()
    result = spies.failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "REJECTED"
    assert result["pre_dispatch"] is True
    assert result.get("reconcile_required") is not True
    assert operator_reason_from_result(result) is not None


# Боевой случай: креативы уже загружены, значит внешняя граница пересекалась —
# и следующий вызов отвергнут браузером до отправки. Прошлые вызовы завершились
# определённо, этот не начинался: исход остаётся REJECTED, а не превращается в
# ручную сверку пустого кабинета.
@pytest.mark.asyncio
async def test_creator_task_rejected_after_uploads_is_still_rejected(monkeypatch) -> None:
    spies = await _run_creator_until_rejection(
        monkeypatch,
        reason_code=_UNRETRYABLE_REASON,
        external_started=True,
    )

    spies.retry.assert_not_awaited()
    spies.failed.assert_awaited_once()
    result = spies.failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "REJECTED"
    assert result.get("reconcile_required") is not True
    assert result.get("manual_review_required") is not True
    assert operator_reason_from_result(result) is not None


# ====================== язык оператора: сначала причина, потом вывод ======================


# Одно и то же событие в двух полосах должно читаться одинаково. Порядок
# «Повтор не поможет. Браузер отказал…» ставил вывод раньше факта, и залив
# рассказывал ту же историю задом наперёд относительно паузы.
def test_both_lanes_name_the_cause_before_the_retry_policy() -> None:
    named = BROWSER_OPERATION_REJECTION_REASONS[_UNRETRYABLE_REASON]
    campaign_text = campaign_operator_reason(
        reason_code="browser_rejection_not_retryable",
        failed_step="uploading",
        rejection_reason_code=_UNRETRYABLE_REASON,
    )
    money_text = browser_rejection_not_retryable_reason(_UNRETRYABLE_REASON)

    for text in (campaign_text, money_text):
        assert text is not None
        assert named in text
        assert text.index(named) < text.index("Повтор той же задачи не поможет")
        assert text.rstrip(".").endswith("Повтор той же задачи не поможет")


# ====================== карточка CRITICAL: куда идти чинить ======================


async def _autostop_card(monkeypatch, exc: BaseException) -> dict:
    """Карточка, которую увидит оператор по одному недоставленному авто-стопу."""
    import core.meta_api.autostop_alert as autostop_alert

    spy_notify = AsyncMock(return_value=True)
    monkeypatch.setattr(autostop_alert, "notify_recurring_incident", spy_notify)
    created = await autostop_alert.maybe_alert_autostop_channel_down(
        exc=exc,
        fb_ad_id="123",
        engine=object(),
    )
    # Money-сигнал не зависит от формулировки: объявление продолжает тратить
    # бюджет в обоих случаях, поэтому инцидент обязан создаваться всегда.
    assert created is True
    spy_notify.assert_awaited_once()
    return spy_notify.await_args.kwargs


@pytest.mark.asyncio
async def test_dead_channel_incident_still_sends_operator_to_the_channel(monkeypatch) -> None:
    from core.meta_api.errors import TemporaryError

    card = await _autostop_card(monkeypatch, TemporaryError("Failed to fetch", code=-2))

    assert card["severity"] == "critical"
    assert any("Vision" in line for line in card["lines"])


# Браузер ответил и отказал сам — канал жив. Отправлять оператора чинить
# browser-agent и профиль Vision значит увести его от настоящей поломки.
@pytest.mark.asyncio
async def test_caller_rejection_incident_does_not_send_operator_to_fix_the_channel(
    monkeypatch,
) -> None:
    card = await _autostop_card(monkeypatch, _rejection(_UNRETRYABLE_REASON))

    assert card["severity"] == "critical"
    assert card["risk"]
    assert not any("Проверь browser-agent" in line for line in card["lines"])
    # Ручное выключение объявления остаётся: деньги идут в обоих случаях.
    assert any("вручную" in line for line in card["lines"])
    # Причина названа словами оператора, а не машинным кодом.
    text = f"{card['title']} {card['summary']} " + " ".join(card["lines"])
    assert BROWSER_OPERATION_REJECTION_REASONS[_UNRETRYABLE_REASON] in text
    assert _UNRETRYABLE_REASON not in text
