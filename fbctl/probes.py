"""Runtime evidence checks used by deploy, doctor, status and restart."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, TypeVar

from fbctl.errors import FbctlError

T = TypeVar("T")


class ProbeClient(Protocol):
    def json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]: ...

    def status(self, url: str, *, timeout: float = 15) -> int: ...

    def patch_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]: ...

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibProbeClient:
    """Dependency-free HTTP adapter with bounded reads and sanitized failures."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]:
        request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        status, payload = self._read(request, timeout=timeout)
        try:
            return status, json.loads(payload)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FbctlError(f"endpoint returned invalid JSON: {_safe_url(url)}") from exc

    def status(self, url: str, *, timeout: float = 15) -> int:
        request = urllib.request.Request(url, method="GET")
        status, _payload = self._read(request, timeout=timeout)
        return status

    def patch_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]:
        return self._send_json("PATCH", url, payload, headers=headers, timeout=timeout)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]:
        return self._send_json("POST", url, payload, headers=headers, timeout=timeout)

    def _send_json(
        self,
        method: str,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None,
        timeout: float,
    ) -> tuple[int, object]:
        request_headers = {"Content-Type": "application/json", **dict(headers or {})}
        request = urllib.request.Request(
            url,
            data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method=method,
        )
        status, response = self._read(request, timeout=timeout)
        try:
            return status, json.loads(response)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FbctlError(f"endpoint returned invalid JSON: {_safe_url(url)}") from exc

    def _read(self, request: urllib.request.Request, *, timeout: float) -> tuple[int, bytes]:
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return response.status, response.read(1_000_001)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(1_000_001)
        except (OSError, urllib.error.URLError) as exc:
            raise FbctlError(f"endpoint is unavailable: {_safe_url(request.full_url)}") from exc


def wait_for(
    description: str,
    check: Callable[[], T],
    *,
    timeout: float,
    interval: float = 2,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    deadline = monotonic() + timeout
    last_error: FbctlError | None = None
    while True:
        try:
            return check()
        except FbctlError as exc:
            last_error = exc
        if monotonic() >= deadline:
            detail = str(last_error) if last_error is not None else "no evidence"
            raise FbctlError(f"timed out waiting for {description}: {detail}") from last_error
        sleep(interval)


def api_headers(api_key: str) -> dict[str, str]:
    if len(api_key) < 24 or "\r" in api_key or "\n" in api_key:
        raise FbctlError("API key does not satisfy the production contract")
    return {"X-API-Key": api_key}


def require_ok_status(client: ProbeClient, url: str) -> None:
    status = client.status(url)
    if status != 200:
        raise FbctlError(f"endpoint returned HTTP {status}: {_safe_url(url)}")


def require_openapi(client: ProbeClient, api_origin: str) -> None:
    status, payload = client.json(f"{api_origin}/openapi.json")
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError("OpenAPI endpoint is unavailable")
    paths = payload.get("paths")
    required = {
        "/api/operator/snapshot",
        "/api/v1/integrations/telegram/webhook",
        "/api/settings/vision",
        "/api/settings/observer/scanning",
    }
    if not isinstance(paths, dict) or not required.issubset(paths):
        raise FbctlError("OpenAPI is missing a required production path")


def require_operator_snapshot(client: ProbeClient, api_origin: str, api_key: str) -> None:
    status, payload = client.json(
        f"{api_origin}/api/operator/snapshot",
        headers=api_headers(api_key),
        timeout=20,
    )
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError("authenticated operator snapshot is unavailable")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or not meta.get("revision") or "generated_at" not in meta:
        raise FbctlError("operator snapshot metadata is incomplete")
    for name in ("attention", "portfolio", "economy", "funnel", "actions", "system"):
        section = payload.get(name)
        if not isinstance(section, dict):
            raise FbctlError(f"operator snapshot section is missing: {name}")
        if section.get("state") not in {
            "ready",
            "empty",
            "partial",
            "stale",
            "unavailable",
        }:
            raise FbctlError(f"operator snapshot section has invalid state: {name}")
        if not isinstance(section.get("sources"), list) or not isinstance(
            section.get("issues"), list
        ):
            raise FbctlError(f"operator snapshot evidence is incomplete: {name}")


def require_exact_browser(client: ProbeClient, api_origin: str, api_key: str) -> None:
    status, payload = client.json(
        f"{api_origin}/api/settings/vision",
        headers=api_headers(api_key),
        timeout=40,
    )
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError("browser readiness endpoint is unavailable")
    required_contract = payload.get("required_browser_contract_version")
    observed_contract = payload.get("browser_contract_version")
    profile_id = payload.get("profile_id")
    live_profile_id = payload.get("live_profile_id")
    if required_contract != 5 or observed_contract != required_contract:
        raise FbctlError("browser-agent contract v5 is not confirmed")
    if payload.get("browser_contract_compatible") is not True:
        raise FbctlError("browser-agent contract is incompatible")
    if not isinstance(profile_id, str) or not profile_id or live_profile_id != profile_id:
        raise FbctlError("live Vision profile does not match canonical configuration")
    if (
        payload.get("graph_probe_performed") is not True
        or payload.get("graph_probe_ok") is not True
    ):
        raise FbctlError("browser-agent did not confirm the required Graph probe")
    if payload.get("channel_status") != "READY" or not payload.get("browser_session_id"):
        raise FbctlError("browser channel is not READY with a concrete session")


def ensure_browser_channel(client: ProbeClient, api_origin: str, api_key: str) -> None:
    """Поднять браузерный канал после пересоздания стола.

    Каждый деплой пересоздаёт контейнер стола, и Vision возвращается без
    запущенного профиля. Ручка идемпотентна: готовый канал она не трогает.
    """
    status, payload = client.post_json(
        f"{api_origin}/api/vision/ensure-cdp",
        {},
        headers=api_headers(api_key),
        timeout=120,
    )
    if status != 200 or not isinstance(payload, dict):
        # Код в сообщении обязателен: 401 (протухший API_KEY), 404 (роутер не
        # подключён) и 502 чинятся по-разному, а во время простоя это
        # единственная диагностика у владельца.
        raise FbctlError(f"browser channel healer returned HTTP {status}")
    if payload.get("ok") is True:
        return
    message = payload.get("message")
    detail = str(message) if isinstance(message, str) and message else str(payload.get("status"))
    raise FbctlError(f"browser channel is not ready ({detail})")


# Состояния, которыми управляет владелец, а не деплой. Выключенное
# сканирование — его решение: обновлять приложение при выключенном скане
# законно, и релиз не должен из-за этого зависать перед promote. Всё
# остальное в блокерах — настоящий дефект рантайма и валит деплой.
OWNER_CONTROLLED_BLOCKERS = frozenset({"scanning_paused"})


def require_system_ready(client: ProbeClient, api_origin: str) -> tuple[str, ...]:
    """Проверить money-контур и вернуть осознанные паузы как предупреждения.

    Эндпоинт отвечает 503, пока есть хоть один блокер, поэтому судим по телу,
    а не по коду: иначе выключенный владельцем скан читается как поломка.
    """
    status, payload = client.json(f"{api_origin}/system-readyz", timeout=20)
    if status not in (200, 503) or not isinstance(payload, dict):
        raise FbctlError("money control plane readiness is unavailable")
    if payload.get("infrastructure_ready") is not True:
        raise FbctlError("money control plane infrastructure is not ready")
    raw_blockers = payload.get("blockers")
    blockers = tuple(str(item) for item in raw_blockers) if isinstance(raw_blockers, list) else ()
    defects = tuple(item for item in blockers if item not in OWNER_CONTROLLED_BLOCKERS)
    if defects:
        raise FbctlError(f"money control plane is not ready ({','.join(defects)})")
    raw_degraded = payload.get("degraded")
    degraded = tuple(str(item) for item in raw_degraded) if isinstance(raw_degraded, list) else ()
    if degraded:
        raise FbctlError(f"money control plane is degraded ({','.join(degraded)})")
    return tuple(item for item in blockers if item in OWNER_CONTROLLED_BLOCKERS)


def enable_observer_scanning(client: ProbeClient, api_origin: str, api_key: str) -> None:
    status, payload = client.patch_json(
        f"{api_origin}/api/settings/observer/scanning",
        {"enabled": True},
        headers=api_headers(api_key),
        timeout=30,
    )
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError(f"owner-approved scanning enable failed with HTTP {status}")
    if payload.get("is_scanning_enabled") is not True:
        raise FbctlError("observer did not confirm scanning is enabled")


def require_telegram_webhook(client: ProbeClient, api_origin: str, api_key: str) -> None:
    status, payload = client.json(
        f"{api_origin}/api/settings/telegram/diagnostics",
        headers=api_headers(api_key),
        timeout=20,
    )
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError("Telegram diagnostics are unavailable")
    if payload.get("webhook_state") != "configured":
        raise FbctlError("Telegram webhook is not configured")
    if payload.get("webhook_remote_url_matches") is not True:
        raise FbctlError("Telegram webhook remote URL is not confirmed")


@dataclass(frozen=True)
class WorkerHeartbeat:
    worker: str
    observed_at: float


def parse_worker_heartbeat(metrics: str, *, expected_worker: str, now: float) -> WorkerHeartbeat:
    pattern = re.compile(
        r'^fb_agent_worker_heartbeat_timestamp_seconds\{worker="([^"]+)"\}\s+([0-9.eE+-]+)$',
        re.MULTILINE,
    )
    matches = [(worker, float(value)) for worker, value in pattern.findall(metrics)]
    for worker, observed_at in matches:
        if worker == expected_worker:
            age = now - observed_at
            if age < -5 or age > 90:
                raise FbctlError(f"worker heartbeat is stale: {expected_worker}")
            return WorkerHeartbeat(worker, observed_at)
    raise FbctlError(f"worker heartbeat metric is missing: {expected_worker}")


def parse_worker_db_poll_success(
    metrics: str,
    *,
    expected_worker: str,
    now: float,
) -> WorkerHeartbeat:
    pattern = re.compile(
        r'^fb_agent_worker_db_poll_success_timestamp_seconds\{worker="([^"]+)"\}\s+'
        r"([0-9.eE+-]+)$",
        re.MULTILINE,
    )
    matches = [(worker, float(value)) for worker, value in pattern.findall(metrics)]
    exact = [observed_at for worker, observed_at in matches if worker == expected_worker]
    if len(exact) != 1:
        raise FbctlError(f"worker DB-poll evidence is missing or duplicated: {expected_worker}")
    observed_at = exact[0]
    age = now - observed_at
    if age < -5 or age > 90:
        raise FbctlError(f"worker DB-poll evidence is stale: {expected_worker}")
    return WorkerHeartbeat(expected_worker, observed_at)


def _safe_url(url: str) -> str:
    return url.split("?", 1)[0]
