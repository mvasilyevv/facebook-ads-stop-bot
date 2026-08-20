# -*- coding: utf-8 -*-
"""#202: money-операция идёт в той сессии браузера, которую назвала очередь.

Гейт готовности отдаёт задачу под конкретную браузерную сессию: именно в ней
подтверждён живой контракт, профиль Vision и токен. Воркер авто-стопа этой
сессией не пользовался — работал со своей текущей, а пустой ``session_id``
означает «browser-agent, выбери сам самую свежую». Между claim и первым RPC
предпочитаемая сессия меняется (перезапуск observer'а, восстановление), и
пауза уходила в другой сессии: слив не остановлен, а чужая реклама тронута.

Здесь фиксируются три вещи, каждая отдельно:

1. воркер закрепляет сессию из claim на время задачи и возвращает прежнюю
   после — включая случай, когда задача упала;
2. ответ браузера из другой сессии отвергает операцию ДО отправки мутации;
   доказательство — двухпризнаковая отметка ``GraphDispatchRecord``
   (``observed and not dispatched``), а не отдельный признак «мы так думаем»;
3. реестр: множество money-capable RPC и множество закрепляющих вызовов —
   не два независимых списка, которые «пока совпадают». Новый money-RPC без
   закрепляющего вызова валит прогон.

Терминальный исход этого отказа (``REJECTED``, не ``UNKNOWN``) закреплён
отдельно и не дублируется здесь:
``tests/unit/test_browser_rejection_retry_policy.py`` — маршрутизация в воркере,
``tests/integration/test_browser_readiness_gate.py`` — запись в task_queue.
"""

from __future__ import annotations

import ast
import asyncio
import uuid
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.campaign_creator_worker.main as campaign_worker
import apps.meta_api_worker.main as meta_worker
from core.meta_api.client import (
    _AUTHORIZED_OPERATION_CALLERS,
    _OPERATION_RPC_TTL_SECONDS,
    BROWSER_CONTRACT_VERSION,
    MetaApiClient,
)
from core.meta_api.dispatch import observe_graph_dispatch
from core.meta_api.errors import BrowserReadinessRejectedError, PermanentError
from core.meta_api.operation_authority import _RPCS as _DURABLE_CONSUME_RPCS
from core.meta_api.upload import MediaUploader

_CAPABILITY_SECRET = "money-session-pinning-secret-" + ("s" * 48)
_PROFILE_ID = "vision-profile-1"
_CLAIMED_SESSION = "session-named-by-the-claim"
_FOREIGN_SESSION = "session-the-browser-picked-instead"
_ACCOUNT_ID = "123"
_TARGET_ID = "987654321"


# ====================== 1. воркер закрепляет сессию из claim ======================


def _money_claim(*, browser_session_id: str | None = _CLAIMED_SESSION) -> SimpleNamespace:
    """Claim ровно той формы, какую отдаёт гейт готовности money-полосе."""
    task = SimpleNamespace(
        id=202,
        task_type="meta_api_mutation",
        requested_by="bot_auto_stop",
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000202"),
        lease_token=5,
    )
    return SimpleNamespace(
        task=task,
        queue_empty=False,
        browser_profile_id=_PROFILE_ID,
        browser_session_id=browser_session_id,
        browser_readiness_generation=3,
    )


def _pinned_client(previous_session_id: str = "") -> MagicMock:
    client = MagicMock()
    client.session_id = previous_session_id
    client.operation_authority.return_value = nullcontext()
    return client


@pytest.mark.asyncio
async def test_money_worker_pins_the_session_named_by_the_claim(monkeypatch) -> None:
    """Падает на коде до #202: воркер работал со своей текущей сессией (пустой)."""
    stop = asyncio.Event()
    monkeypatch.setattr(
        meta_worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(return_value=_money_claim()),
    )
    monkeypatch.setattr(meta_worker, "record_worker_heartbeat", AsyncMock())
    client = _pinned_client(previous_session_id="session-left-over-from-idle-polling")
    session_during_task: list[str] = []

    async def process(*_args, **_kwargs) -> None:
        session_during_task.append(client.session_id)
        stop.set()

    monkeypatch.setattr(meta_worker, "process_one_task", process)

    await meta_worker.task_loop(object(), stop, client=client, alert_ctx=None)

    assert session_during_task == [_CLAIMED_SESSION]
    # Закрепление живёт ровно столько, сколько задача: клиент между задачами общий.
    assert client.session_id == "session-left-over-from-idle-polling"


@pytest.mark.asyncio
async def test_money_worker_restores_the_previous_session_when_the_task_fails(
    monkeypatch,
) -> None:
    """Сорвавшаяся задача не оставляет чужую сессию закреплённой на клиенте."""
    stop = asyncio.Event()
    monkeypatch.setattr(
        meta_worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(return_value=_money_claim()),
    )
    monkeypatch.setattr(meta_worker, "record_worker_heartbeat", AsyncMock())
    client = _pinned_client(previous_session_id="session-before-the-claim")

    async def process(*_args, **_kwargs) -> None:
        stop.set()
        raise RuntimeError("задача сорвалась внутри money-окна")

    monkeypatch.setattr(meta_worker, "process_one_task", process)

    with pytest.raises(RuntimeError, match="сорвалась"):
        await meta_worker.task_loop(object(), stop, client=client, alert_ctx=None)

    assert client.session_id == "session-before-the-claim"


@pytest.mark.asyncio
async def test_money_worker_refuses_a_claim_without_a_browser_session(monkeypatch) -> None:
    """Claim без сессии — не повод «выбрать самую свежую»: работать негде."""
    stop = asyncio.Event()
    monkeypatch.setattr(
        meta_worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(return_value=_money_claim(browser_session_id="  ")),
    )
    monkeypatch.setattr(meta_worker, "record_worker_heartbeat", AsyncMock())
    client = _pinned_client()
    processed: list[str] = []

    async def process(*_args, **_kwargs) -> None:
        # Останавливает цикл вместо того, чтобы дать ему крутиться: если гарда
        # нет, тест обязан упасть отказом «не бросил», а не зависнуть.
        processed.append(client.session_id)
        stop.set()

    monkeypatch.setattr(meta_worker, "process_one_task", process)

    with pytest.raises(RuntimeError, match="browser session"):
        await meta_worker.task_loop(object(), stop, client=client, alert_ctx=None)

    assert processed == []
    client.operation_authority.assert_not_called()
    assert client.session_id == ""


# ============ 2. ответ из другой сессии отвергает операцию до отправки ============


class _Transport:
    """Двойник browser-agent: называет свою сессию и считает уходящие запросы."""

    def __init__(self, *, session_id: str) -> None:
        self._session_id = session_id
        self.dispatched: list[str] = []

    async def CheckMetaApiHealth(self, request, **_kwargs):  # noqa: N802 — имя RPC
        return SimpleNamespace(
            healthy=True,
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            probe_performed=True,
            probe_detail="ok",
            session_id=self._session_id,
            vision_profile_id=_PROFILE_ID,
        )

    async def ExecuteGraphCallV5(self, _request, **_kwargs):  # noqa: N802 — имя RPC
        self.dispatched.append("execute_graph_call")
        raise AssertionError("Graph-запрос ушёл в браузер после отказа по сессии")

    async def UploadImage(self, _request, **_kwargs):  # noqa: N802 — имя RPC
        self.dispatched.append("upload_image")
        raise AssertionError("картинка ушла в браузер после отказа по сессии")

    async def UploadVideo(self, chunks, **_kwargs):  # noqa: N802 — имя RPC
        async for _chunk in chunks:
            self.dispatched.append("upload_video")
        raise AssertionError("видео ушло в браузер после отказа по сессии")


async def _probe_execute_graph_call(client: MetaApiClient) -> None:
    """Ровно тот запрос, который строит обработчик ``pause_ad``."""
    await client.execute_graph_call(
        method="POST",
        endpoint=f"/{_TARGET_ID}",
        query_params={"status": "PAUSED"},
        ad_account_id=_ACCOUNT_ID,
    )


async def _probe_upload_image(client: MetaApiClient) -> None:
    await MediaUploader(client).upload_image(_ACCOUNT_ID, b"pixels")


async def _probe_upload_video(client: MetaApiClient) -> None:
    await MediaUploader(client).upload_video_from_bytes(_ACCOUNT_ID, b"frames")


@dataclass(frozen=True)
class _MoneyRpc:
    """Money-capable RPC: где он закрепляет сессию и как его позвать."""

    # Функции, которые подписывают одноразовый грант именно этого RPC. Грант
    # выдаёт единственная точка — ``MetaApiClient.prepare_operation_authorization``,
    # и она же сверяет ответ браузера с закреплённой сессией.
    pinning_sites: frozenset[str]
    caller: str
    probe: Callable[[MetaApiClient], object]
    # Идёт ли RPC через ``execute_graph_call``: только у него есть отметка
    # «вызов вёл настоящий клиент Graph API», по которой отказ до отправки
    # становится доказанным, а не предполагаемым.
    through_graph_client: bool


# Реестр. Ключи обязаны совпадать с боевым перечнем money-capable RPC —
# см. test_money_capable_rpc_registry_matches_the_pinning_call_sites.
_MONEY_RPC_REGISTRY: dict[str, _MoneyRpc] = {
    "execute_graph_call": _MoneyRpc(
        pinning_sites=frozenset({"core.meta_api.client:MetaApiClient.execute_graph_call"}),
        caller="autopause",
        probe=_probe_execute_graph_call,
        through_graph_client=True,
    ),
    "upload_image": _MoneyRpc(
        pinning_sites=frozenset({"core.meta_api.upload:MediaUploader.upload_image"}),
        caller="campaign_creator",
        probe=_probe_upload_image,
        through_graph_client=False,
    ),
    "upload_video": _MoneyRpc(
        pinning_sites=frozenset(
            {
                "core.meta_api.upload:MediaUploader._video_chunks",
                "core.meta_api.upload:MediaUploader._video_chunks_from_bytes",
            }
        ),
        caller="campaign_creator",
        probe=_probe_upload_video,
        through_graph_client=False,
    ),
}


@pytest.mark.parametrize("rpc", sorted(_MONEY_RPC_REGISTRY))
@pytest.mark.asyncio
async def test_money_rpc_rejects_a_foreign_session_before_dispatch(
    monkeypatch,
    rpc: str,
) -> None:
    """Браузер назвал другую сессию — операция отвергнута, наружу не ушло ничего."""
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _CAPABILITY_SECRET)
    entry = _MONEY_RPC_REGISTRY[rpc]
    transport = _Transport(session_id=_FOREIGN_SESSION)
    client = MetaApiClient(session_id=_CLAIMED_SESSION)
    client._stub = transport  # noqa: SLF001 — двойник транспорта вместо gRPC-канала

    with client.operation_authority(
        caller=entry.caller,
        task_id=202,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000202"),
        lease_token=5,
        vision_profile_id=_PROFILE_ID,
    ):
        with observe_graph_dispatch() as record:
            with pytest.raises(BrowserReadinessRejectedError):
                await entry.probe(client)

    assert transport.dispatched == []
    assert record.dispatched is False
    if entry.through_graph_client:
        # Доказано, что запрос НЕ уходил, а не «мы не видели, чтобы уходил».
        assert record.proven_not_dispatched is True


def _live_authority_row() -> dict[str, object]:
    """Строка живой аренды в том виде, в каком её отдаёт PostgreSQL воркеру."""
    return {
        "task_type": "meta_api_mutation",
        "lane": "money",
        "requested_by": "bot_auto_stop",
        "payload": {
            "mutation_kind": "pause_ad",
            "target_id": _TARGET_ID,
            "ad_account_id": _ACCOUNT_ID,
        },
        "result": {},
        "db_now_epoch": 1_800_000_000,
        "lease_expires_epoch": 1_800_000_030,
        "deadline_epoch": 1_800_000_090,
        "bound_ad_account_id": f"act_{_ACCOUNT_ID}",
    }


def _authority_engine() -> MagicMock:
    """Двойник PostgreSQL-выдачи гранта: аренда жива, грант выписывается."""
    live_result = MagicMock()
    live_result.mappings.return_value.one_or_none.return_value = _live_authority_row()

    async def execute(statement: object, _params: object = None) -> MagicMock:
        if "FROM task_queue AS tq" in str(statement):
            return live_result
        return MagicMock()

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    return engine


@pytest.mark.asyncio
async def test_money_pause_in_a_foreign_session_never_reaches_the_browser(
    monkeypatch,
) -> None:
    """Сквозной money-путь: воркер → клиент → транспорт.

    Падает на коде до #202: без закрепления клиент не с чем сравнивать ответ
    браузера, грант выписывается на чужую сессию, и POST со сменой статуса
    уходит в неё. Оба признака отказа проверяются вместе — исключение той
    семьи, что возвращает задачу в очередь и гасит устаревшую готовность, и
    пустой транспорт.
    """
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _CAPABILITY_SECRET)
    # Полоса money обслуживается единственным consumer'ом: в этом процессе имя
    # воркера — константа, поэтому вызывающего называем явно.
    monkeypatch.setattr(meta_worker, "_BROWSER_OPERATION_CALLER", "autopause")
    stop = asyncio.Event()
    monkeypatch.setattr(
        meta_worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(return_value=_money_claim()),
    )
    monkeypatch.setattr(meta_worker, "record_worker_heartbeat", AsyncMock())

    transport = _Transport(session_id=_FOREIGN_SESSION)
    # Клиент строится ровно как в ``_build_meta_client``: своей сессии у него нет,
    # её обязан назначить claim.
    client = MetaApiClient(operation_engine=_authority_engine())
    client._stub = transport  # noqa: SLF001 — двойник транспорта вместо gRPC-канала
    outcome: list[str] = []

    async def process(_engine, _task, *, client, alert_ctx=None) -> None:
        try:
            await _probe_execute_graph_call(client)
        except Exception as exc:  # noqa: BLE001 — важен именно класс отказа
            outcome.append(type(exc).__name__)
        else:
            outcome.append("dispatched")
        stop.set()

    monkeypatch.setattr(meta_worker, "process_one_task", process)

    await meta_worker.task_loop(object(), stop, client=client, alert_ctx=None)

    assert transport.dispatched == []
    assert outcome == [BrowserReadinessRejectedError.__name__]


@pytest.mark.asyncio
async def test_money_rpc_accepts_the_session_named_by_the_claim(monkeypatch) -> None:
    """Контрольный: та же сессия отказа не вызывает — отвергается именно чужая."""
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _CAPABILITY_SECRET)
    transport = _Transport(session_id=_CLAIMED_SESSION)
    client = MetaApiClient(session_id=_CLAIMED_SESSION)
    client._stub = transport  # noqa: SLF001 — двойник транспорта вместо gRPC-канала

    with client.operation_authority(
        caller="autopause",
        task_id=202,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000202"),
        lease_token=5,
        vision_profile_id=_PROFILE_ID,
    ):
        # Совпавшая сессия проходит проверку идентичности и упирается уже в
        # следующий гейт — PostgreSQL-выдачу гранта, которой у этого клиента нет.
        # Класс отказа именно этого гейта и доказывает, что проверка сессии
        # осталась позади и пропустила свою сессию.
        with pytest.raises(PermanentError, match="PostgreSQL operation authority"):
            await _probe_execute_graph_call(client)

    assert transport.dispatched == []


# ====================== 3. реестр money-RPC ↔ закрепляющие вызовы ======================

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Каталоги Python-исходников, которые не являются боевым кодом этого дерева.
# Скрытые каталоги перечислять поимённо нельзя: рядом живут вложенные worktree
# (`.claude/worktrees/…`) с полной копией `core/`, и обход находил в них второй
# экземпляр того же вызова. Копия списка расходится молча — правило вместо
# перечисления (канон 6.1).
_SKIPPED_TREES = frozenset(
    {
        "node_modules",
        "tests",
        "frontend",
        "frontend-mini",
        "services",
        "packages",
    }
)


def _python_sources() -> Iterator[Path]:
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        relative = path.relative_to(_REPO_ROOT)
        if _SKIPPED_TREES & set(relative.parts):
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        yield path


def _qualname(stack: list[str]) -> str:
    return ".".join(stack)


def _collect_module_sites(
    node: ast.AST,
    *,
    module: str,
    stack: list[str],
    sites: dict[str, set[str]],
) -> None:
    for child in ast.iter_child_nodes(node):
        named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        if named:
            stack.append(child.name)  # type: ignore[attr-defined]
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "prepare_operation_authorization"
        ):
            site = f"{module}:{_qualname(stack)}"
            rpc_values = [keyword.value for keyword in child.keywords if keyword.arg == "rpc"]
            assert len(rpc_values) == 1, f"{site} подписывает грант без явного rpc="
            literal = rpc_values[0]
            assert isinstance(literal, ast.Constant) and isinstance(literal.value, str), (
                f"{site} называет rpc не строковым литералом — реестр не может сверить такой вызов"
            )
            sites.setdefault(literal.value, set()).add(site)
        _collect_module_sites(child, module=module, stack=stack, sites=sites)
        if named:
            stack.pop()


def _discovered_pinning_sites() -> dict[str, set[str]]:
    """Найти в дереве все вызовы выдачи гранта и назвать RPC каждого.

    Реестр ниже сверяется с этой картой, а не с памятью автора: money-RPC,
    добавленный в любом модуле, попадёт сюда сам и не сойдётся с реестром.
    """
    sites: dict[str, set[str]] = {}
    for path in _python_sources():
        module = str(path.relative_to(_REPO_ROOT).with_suffix("")).replace("/", ".")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _collect_module_sites(tree, module=module, stack=[], sites=sites)
    return sites


def test_money_capable_rpc_lists_do_not_drift_apart() -> None:
    """Подпись гранта и его durable-погашение перечисляют один и тот же набор."""
    assert set(_OPERATION_RPC_TTL_SECONDS) == set(_DURABLE_CONSUME_RPCS)


def test_money_capable_rpc_registry_matches_the_pinning_call_sites() -> None:
    """Новый money-RPC без закрепляющего вызова (и наоборот) валит прогон."""
    money_rpcs = set(_OPERATION_RPC_TTL_SECONDS)
    assert set(_MONEY_RPC_REGISTRY) == money_rpcs, (
        "реестр закрепления разошёлся с боевым перечнем money-capable RPC"
    )

    discovered = _discovered_pinning_sites()
    assert set(discovered) == money_rpcs, (
        "в дереве подписывается грант на RPC вне боевого перечня money-capable"
    )
    declared = {rpc: set(entry.pinning_sites) for rpc, entry in _MONEY_RPC_REGISTRY.items()}
    assert discovered == declared


def test_every_money_capable_caller_has_a_registered_pinning_worker() -> None:
    """Новый вызывающий money-операций обязан прийти со своим закреплением."""
    assert set(_MONEY_CALLER_PINNING_WORKERS) == set(_AUTHORIZED_OPERATION_CALLERS)


async def _drive_meta_api_worker(monkeypatch) -> tuple[list[str], str]:
    stop = asyncio.Event()
    monkeypatch.setattr(
        meta_worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(return_value=_money_claim()),
    )
    monkeypatch.setattr(meta_worker, "record_worker_heartbeat", AsyncMock())
    client = _pinned_client()
    seen: list[str] = []

    async def process(*_args, **_kwargs) -> None:
        seen.append(client.session_id)
        stop.set()

    monkeypatch.setattr(meta_worker, "process_one_task", process)
    await meta_worker.task_loop(object(), stop, client=client, alert_ctx=None)
    return seen, client.session_id


async def _drive_campaign_worker(monkeypatch) -> tuple[list[str], str]:
    stop = asyncio.Event()
    monkeypatch.setattr(
        campaign_worker,
        "_claim",
        AsyncMock(return_value=_money_claim()),
    )
    monkeypatch.setattr(campaign_worker, "record_worker_heartbeat", AsyncMock())
    client = _pinned_client()
    seen: list[str] = []

    async def process(*_args, **_kwargs) -> None:
        seen.append(client.session_id)
        stop.set()

    monkeypatch.setattr(campaign_worker, "process_one_task", process)
    await campaign_worker.task_loop(object(), stop, client=client, uploader=object())
    return seen, client.session_id


# Реестр вызывающих: у каждого money-capable caller есть воркер, который
# закрепляет сессию из claim. Именно этой строки не хватало авто-стопу.
_MONEY_CALLER_PINNING_WORKERS: dict[str, Callable[[object], object]] = {
    "autopause": _drive_meta_api_worker,
    "meta_api": _drive_meta_api_worker,
    "campaign_creator": _drive_campaign_worker,
}


@pytest.mark.parametrize("caller", sorted(_MONEY_CALLER_PINNING_WORKERS))
@pytest.mark.asyncio
async def test_registered_pinning_worker_really_pins_the_claimed_session(
    monkeypatch,
    caller: str,
) -> None:
    """Запись в реестре не верится на слово: воркер прогоняется и проверяется."""
    seen, after = await _MONEY_CALLER_PINNING_WORKERS[caller](monkeypatch)

    assert seen == [_CLAIMED_SESSION]
    assert after == ""
