# -*- coding: utf-8 -*-
"""SyntxClient — async-клиент прямого API syntx.ai (без UI/браузера).

Контракт снят вживую 16.06 (см. reference-syntx-api-direct в памяти):
  base: https://api.syntx.ai/api/v1, авторизация Authorization: Bearer <JWT>.
  Цикл генерации:
    1) POST /chats/upload-files   (multipart files=..., check_duplicates=true) → r2 url рефа
    2) POST /chats                {"title","scope":"image"}                     → uuid чата
    3) POST /design/generate?ai_name=X  {chat_uuid, prompt, settings{...}}       → message+task
    4) GET  /chats/{uuid}/inprogress    пусто [] == готово (поллинг)
    5) GET  /chats/{uuid}/messages      результат в message_object[].object_url (/generated/)
  Каталог: GET /ai, GET /ai/models, GET /v2/get_model_info (цена в токенах).
  Аккаунт: GET /user/balance.

Расход токенов логируется как дельта баланса до/после генерации (точно, без
угадывания cost-params). Видео-путь заложен (generate_video) но выключен —
включить после живого теста поллинга/модерации видео-моделей.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.syntx.analysis import (
    DEFAULT_ANALYSIS_POOL,
    AnalysisResult,
    parse_analysis_json,
)
from core.syntx.auth import resolve_syntx_token, token_days_left
from core.syntx.catalog import ModelCatalog
from core.syntx.errors import (
    SyntxError,
    SyntxGenerationError,
    SyntxGenerationTimeout,
    SyntxModerationError,
    TemporaryError,
    classify_http_error,
    looks_like_moderation,
)
from core.syntx.models import (
    SCOPE_IMAGE,
    SCOPE_VIDEO,
    Balance,
    GenRequest,
    GenResult,
    UploadedRef,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.syntx.ai"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_POLL_INTERVAL = 3.0
_DEFAULT_POLL_TIMEOUT = 300.0
# Меньше этого числа дней до exp — предупреждаем в лог (токен пора обновить).
_TOKEN_WARN_DAYS = 3.0

# Поля settings, которые КОНКРЕТНАЯ модель реально принимает. Лишние ключи
# (напр. quality/details_quality у banana или flux) → чёрный кадр (проверено 16.06).
_IMAGE_SETTINGS_FIELDS: dict[str, tuple[str, ...]] = {
    "sora-images": ("n", "aspect_ratio", "quality", "details_quality"),
    "banana": ("aspect_ratio", "image_size"),
    "seedream": ("aspect_ratio",),
    "flux": ("aspect_ratio",),
}
_DEFAULT_IMAGE_SETTINGS_FIELDS: tuple[str, ...] = ("aspect_ratio",)


class SyntxClient:
    """Async-клиент syntx.ai. Image-генерация рабочая, video — заложена.

    Usage:
        async with SyntxClient() as cl:
            cat = await cl.list_models()
            res = await cl.generate_image(req, download_to=Path("out.jpg"))
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        poll_interval_seconds: float | None = None,
        poll_timeout_seconds: float | None = None,
    ) -> None:
        settings = _safe_settings()
        self._token = resolve_syntx_token(token)
        self._base_url = (
            base_url or getattr(settings, "syntx_base_url", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "syntx_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        )
        self._poll_interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else getattr(settings, "syntx_poll_interval_seconds", _DEFAULT_POLL_INTERVAL)
        )
        self._poll_timeout = (
            poll_timeout_seconds
            if poll_timeout_seconds is not None
            else getattr(settings, "syntx_poll_timeout_seconds", _DEFAULT_POLL_TIMEOUT)
        )
        self._external_client = http_client is not None
        self._http: httpx.AsyncClient | None = http_client
        self._max_retries = max(1, max_retries)

    # ====================== lifecycle ======================

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        days = token_days_left(self._token)
        if days is not None and days <= _TOKEN_WARN_DAYS:
            logger.warning(
                "syntx auth_token протухает через %.1f дн — обнови localStorage.auth_token", days
            )
        elif days is not None:
            logger.info("SyntxClient запущен: %s (токен ~%.0f дн)", self._base_url, days)

    async def close(self) -> None:
        if self._http is not None and not self._external_client:
            await self._http.aclose()
        self._http = None

    async def __aenter__(self) -> SyntxClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ====================== account / catalog ======================

    async def get_balance(self) -> Balance:
        data = await self._get("/user/balance")
        return Balance.from_api_payload(data if isinstance(data, dict) else {})

    async def list_models(self, *, lang: str = "ru") -> ModelCatalog:
        """Собрать каталог моделей (GET /ai + /ai/models)."""
        ai_payload, models_payload = await asyncio.gather(
            self._get("/ai", params={"lang": lang}),
            self._get("/ai/models", params={"lang": lang}),
        )
        return ModelCatalog.from_api(
            ai_payload if isinstance(ai_payload, list) else [],
            models_payload if isinstance(models_payload, list) else [],
        )

    async def get_cost(self, ai_name: str, model_type: str, **cost_params: Any) -> float | None:
        """Цена генерации в токенах (GET /v2/get_model_info). None — если API
        требует cost-params, которые мы не передали (вернёт 400 → None)."""
        params: dict[str, Any] = {"ai_name": ai_name, "model_type": model_type, "upscale": 0}
        params.update(cost_params)
        try:
            data = await self._get("/v2/get_model_info", params=params, api_version="v2")
        except SyntxError:
            return None
        if isinstance(data, dict) and "cost" in data:
            try:
                return float(data["cost"])
            except (TypeError, ValueError):
                return None
        return None

    # ====================== generation: image ======================

    async def generate_image(
        self,
        req: GenRequest,
        *,
        download_to: Path | None = None,
    ) -> GenResult:
        """Полный цикл image-генерации: upload → chat → generate → poll → fetch.

        Расход токенов логируется как дельта баланса до/после. download_to: если
        задан — скачать результат(ы) (при нескольких — суффиксы _1/_2).
        """
        if req.scope != SCOPE_IMAGE:
            raise ValueError(f"generate_image: ожидался scope={SCOPE_IMAGE}, дан {req.scope!r}")

        balance_before = await self._safe_balance()
        ref_urls = await self._resolve_ref_urls(req.image_refs)
        mask_resolved = await self._resolve_ref_urls((req.mask_ref,)) if req.mask_ref else []
        mask_url = mask_resolved[0] if mask_resolved else None
        chat_uuid = await self.create_chat(scope=SCOPE_IMAGE)
        settings = self._build_image_settings(req, ref_urls, mask_url)
        message_id = await self._submit(req.ai_name, chat_uuid, req.prompt, settings)
        await self._poll_until_done(chat_uuid)
        urls = await self._fetch_results(chat_uuid)
        if not urls:
            raise SyntxGenerationError(
                f"генерация без результата (chat={chat_uuid}) — возможно модерация",
                endpoint="/chats/messages",
            )

        local_paths = await self._download_all(urls, download_to) if download_to else ()
        tokens_spent = await self._log_spend(balance_before, req)
        return GenResult(
            chat_uuid=chat_uuid,
            ai_name=req.ai_name,
            model_type=req.model_type,
            image_urls=tuple(urls),
            local_paths=tuple(local_paths),
            message_id=message_id,
            tokens_spent=tokens_spent,
        )

    async def generate(self, req: GenRequest, *, download_to: Path | None = None) -> GenResult:
        """Диспетчер по scope. Сейчас рабочий путь только image."""
        if req.scope == SCOPE_IMAGE:
            return await self.generate_image(req, download_to=download_to)
        if req.scope == SCOPE_VIDEO:
            return await self.generate_video(req, download_to=download_to)
        raise NotImplementedError(f"scope {req.scope!r} пока не поддержан клиентом")

    # ====================== editing (Тир 1) ======================

    async def edit_image(
        self,
        image: str,
        instruction: str,
        *,
        mask: str | None = None,
        ai_name: str | None = None,
        model_type: str | None = None,
        image_size: str | None = "2K",
        aspect_ratio: str | None = None,
        download_to: Path | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GenResult:
        """Точечная правка картинки (instruction-edit).

        Дефолт — **Nano Banana (banana3)**: faithful-правка — меняет описанное,
        остальное сохраняет пиксельно (проверено 16.06: «GHANA→KENYA», курица/лого/
        монеты/фон не тронуты). flux-kontext на этом провайдере НЕ faithful (чёрный
        кадр с image_url либо переосмысливает через `<<<url>>>`) — не дефолт.

        `image` — локальный путь или r2-url исходника. `mask` — опц. маска для inpaint
        (экспериментально). `aspect_ratio=None` → сохранить пропорции исходника.
        Banana обычно отдаёт увеличенный кадр — при необходимости докропить
        `core.imaging` под точный формат.
        """
        settings = _safe_settings()
        ai = ai_name or getattr(settings, "syntx_default_edit_ai", "banana")
        model = model_type or getattr(settings, "syntx_default_edit_model", "banana3")
        req = GenRequest(
            scope=SCOPE_IMAGE,
            ai_name=ai,
            model_type=model,
            prompt=instruction,
            image_refs=(image,),
            mask_ref=mask,
            aspect_ratio=aspect_ratio,
            quality=None,
            details_quality=None,
            image_size=image_size,
            extra=extra or {},
        )
        return await self.generate_image(req, download_to=download_to)

    async def upscale_image(self, image: str, *, model_type: str = "clarity") -> GenResult:
        """АПСКЕЙЛ — ЗАЛОЖЕН, пока выключен.

        clarity/magnific — scope `tool_image_upscaler`, идут НЕ через /design/generate
        (наш вызов вернул пустой результат — 16.06). Нужен отдельный endpoint/параметры
        (снять с UI). До этого финальный апскейл — внешними средствами / по запросу.
        """
        raise NotImplementedError(
            "upscale (clarity/magnific) не подключён: tool_image_upscaler идёт "
            "другим каналом, не /design/generate — снять контракт с UI и дописать"
        )

    # ====================== generation: video (заложено) ======================

    async def generate_video(
        self,
        req: GenRequest,
        *,
        download_to: Path | None = None,
    ) -> GenResult:
        """ВИДЕО — ЗАЛОЖЕНО НА БУДУЩЕЕ, пока выключено.

        Контракт снят вживую (veo_omni): settings = {
            type: "references"|"first_frame", image_urls: [...] (мн.ч.!),
            model_type, aspect_ratio, video_duration, upscale, task_to_edit, seed
        }; ai_name = kling|seedance|wan_video|veo3|... ; endpoint тот же
        POST /design/generate?ai_name=X. Поллинг/забор результата — как у image.
        `_build_video_settings` уже готов и покрыт тестом.

        Гемблинг-роутинг (память feedback-syntx-veo-cuts-gambling-video):
        дефолт — kling_image2video; seedance/wan — запас; **veo НЕ для гемблинга**
        (режет по фильтру, кредиты горят). Включить после живого теста модерации
        и таймингов видео-моделей (они длиннее image: повысить poll_timeout).
        """
        raise NotImplementedError(
            "video-генерация заложена, но выключена: включить после живого теста "
            "(см. docstring generate_video — контракт и гемблинг-роутинг готовы)"
        )

    # ====================== analysis (text-vision, только анализ) ======================

    async def analyze_image(
        self,
        image: str,
        prompt: str,
        *,
        ai_name: str = "chatgpt",
        model_type: str = "gpt-5.5",
    ) -> str:
        """Прогнать картинку+промпт через одну text-vision модель → текст ответа.

        Контракт (снят 16.06): create_chat(scope='text') → POST messages с objects
        [{text},{image}] → poll /inprogress → ответ ассистента (author_id == -1).
        Генерацию/правку НЕ вызывает — чистый анализ.
        """
        ref_urls = await self._resolve_ref_urls((image,))
        image_url = ref_urls[0] if ref_urls else None
        chat_uuid = await self.create_chat(scope="text")
        await self._post_text_message(chat_uuid, prompt, image_url, ai_name, model_type)
        await self._poll_until_done(chat_uuid)
        return await self._fetch_assistant_reply(chat_uuid)

    async def analyze_ensemble(
        self,
        image: str,
        prompt: str,
        *,
        models: Sequence[tuple[str, str, str]] | None = None,
    ) -> list[AnalysisResult]:
        """Параллельно прогнать картинку через пул моделей разных лабораторий.

        models — (ai_name, model_type, label); по умолчанию DEFAULT_ANALYSIS_POOL.
        Картинка грузится ОДИН раз, image_url переиспользуется всеми. Падение одной
        модели не валит остальные (ошибка кладётся в AnalysisResult.error).
        """
        pool = list(models) if models is not None else list(DEFAULT_ANALYSIS_POOL)
        ref_urls = await self._resolve_ref_urls((image,))
        image_url = ref_urls[0] if ref_urls else None

        async def _one(ai_name: str, model_type: str, label: str) -> AnalysisResult:
            try:
                chat_uuid = await self.create_chat(scope="text")
                await self._post_text_message(chat_uuid, prompt, image_url, ai_name, model_type)
                await self._poll_until_done(chat_uuid)
                raw = await self._fetch_assistant_reply(chat_uuid)
                return AnalysisResult(
                    label, ai_name, model_type, raw=raw, parsed=parse_analysis_json(raw)
                )
            except SyntxError as exc:
                return AnalysisResult(label, ai_name, model_type, error=str(exc))

        return list(await asyncio.gather(*(_one(a, m, lbl) for a, m, lbl in pool)))

    async def _post_text_message(
        self,
        chat_uuid: str,
        prompt: str,
        image_url: str | None,
        ai_name: str,
        model_type: str,
    ) -> None:
        objects: list[dict[str, Any]] = [
            {
                "object_type": "text",
                "object_url": None,
                "object_text": prompt,
                "model_type": model_type,
            }
        ]
        if image_url:
            objects.append(
                {
                    "object_type": "image",
                    "object_url": image_url,
                    "object_text": "ref",
                    "model_type": model_type,
                }
            )
        await self._post(
            f"/chats/{chat_uuid}/messages", params={"ai_name": ai_name}, json={"objects": objects}
        )

    async def _fetch_assistant_reply(self, chat_uuid: str) -> str:
        """Последний текстовый ответ ассистента (author_id == -1)."""
        data = await self._get(f"/chats/{chat_uuid}/messages", params={"page_size": 20})
        messages = (data or {}).get("messages", []) if isinstance(data, dict) else []
        reply = ""
        for m in messages:
            if not isinstance(m, dict) or m.get("author_id") != -1:
                continue
            for obj in m.get("message_object", []):
                if isinstance(obj, dict) and obj.get("object_type") == "text":
                    reply = obj.get("object_text") or reply
        return reply

    # ====================== low-level steps ======================

    async def upload_files(self, paths: list[str | Path]) -> list[UploadedRef]:
        """Загрузить локальные реф-картинки. Возвращает r2-url для settings."""
        refs: list[UploadedRef] = []
        for path in paths:
            name, data_bytes, mime = await asyncio.to_thread(_read_upload_file, path)
            files = {"files": (name, data_bytes, mime)}
            data = await self._post(
                "/chats/upload-files", files=files, data={"check_duplicates": "true"}
            )
            for row in (data or {}).get("files", []):
                if isinstance(row, dict):
                    refs.append(UploadedRef.from_api_row(row))
        return refs

    async def create_chat(self, *, scope: str = SCOPE_IMAGE, title: str = "api") -> str:
        data = await self._post("/chats", json={"title": title, "scope": scope})
        uuid = (data or {}).get("uuid")
        if not uuid:
            raise SyntxError("create_chat: API не вернул uuid", endpoint="/chats")
        return str(uuid)

    async def _submit(
        self, ai_name: str, chat_uuid: str, prompt: str, settings: dict[str, Any]
    ) -> int | None:
        data = await self._post(
            "/design/generate",
            params={"ai_name": ai_name},
            json={"chat_uuid": chat_uuid, "prompt": prompt, "settings": settings},
        )
        mid = (data or {}).get("id")
        return int(mid) if isinstance(mid, int) else None

    async def _poll_until_done(self, chat_uuid: str) -> None:
        """Поллить /inprogress пока не опустеет либо не выйдет poll_timeout."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._poll_timeout
        await asyncio.sleep(self._poll_interval)  # дать беку зарегистрировать задачу
        while loop.time() < deadline:
            data = await self._get(f"/chats/{chat_uuid}/inprogress")
            if isinstance(data, list) and not data:
                return
            await asyncio.sleep(self._poll_interval)
        raise SyntxGenerationTimeout(
            f"генерация не завершилась за {self._poll_timeout:.0f}с (chat={chat_uuid})",
            endpoint="/chats/inprogress",
        )

    async def _fetch_results(self, chat_uuid: str) -> list[str]:
        """Забрать url'ы результата из /messages (только /generated/).

        Рассчитано на СВЕЖИЙ чат (один submit): берём первую страницу 20 сообщений.
        Для переиспользуемого чата с историей понадобилась бы пагинация.
        """
        data = await self._get(f"/chats/{chat_uuid}/messages", params={"page_size": 20})
        messages = (data or {}).get("messages", []) if isinstance(data, dict) else []
        urls = self._extract_generated_urls(messages)
        if not urls:
            self._raise_if_moderation(messages, chat_uuid)
        return urls

    async def download_url(self, url: str, out: Path) -> Path:
        if self._http is None:
            raise RuntimeError("SyntxClient не запущен: await start()")
        resp = await self._http.get(url)
        resp.raise_for_status()
        await asyncio.to_thread(_write_bytes, out, resp.content)
        return out

    # ====================== pure helpers (тестируемые) ======================

    @staticmethod
    def _build_image_settings(
        req: GenRequest, ref_urls: list[str], mask_url: str | None = None
    ) -> dict[str, Any]:
        """settings для image-генерации/правки — ПЕР-МОДЕЛЬНО.

        Шлём только поля, которые принимает данная модель (_IMAGE_SETTINGS_FIELDS):
        лишние ключи (quality/details_quality у banana/flux) → чёрный кадр.
        image_url — список (контракт upload→r2). mask_url — для inpaint.
        """
        settings: dict[str, Any] = {"model_type": req.model_type}
        fields = _IMAGE_SETTINGS_FIELDS.get(req.ai_name, _DEFAULT_IMAGE_SETTINGS_FIELDS)
        values = {
            "n": req.n,
            "aspect_ratio": req.aspect_ratio,
            "quality": req.quality,
            "details_quality": req.details_quality,
            "image_size": req.image_size,
        }
        for f in fields:
            v = values.get(f)
            if v is not None:
                settings[f] = v
        if ref_urls:
            settings["image_url"] = list(ref_urls)
        if mask_url:
            settings["mask_url"] = mask_url
        settings.update(req.extra)
        return settings

    @staticmethod
    def _build_video_settings(req: GenRequest, ref_urls: list[str]) -> dict[str, Any]:
        """settings для video-генерации (image_urls — PLURAL; + type/duration).

        Заложено на будущее (см. generate_video). Контракт veo_omni снят вживую.
        """
        settings: dict[str, Any] = {
            "model_type": req.model_type,
            "aspect_ratio": req.aspect_ratio,
            "type": req.gen_type or "references",
            "video_duration": req.video_duration or 8,
            "upscale": 0,
        }
        if ref_urls:
            settings["image_urls"] = list(ref_urls)
        settings.update(req.extra)
        return settings

    @staticmethod
    def _extract_generated_urls(messages: list[Any]) -> list[str]:
        """Достать url'ы результата (object_type=image, в url есть /generated/)."""
        urls: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            for obj in m.get("message_object", []):
                if not isinstance(obj, dict):
                    continue
                url = obj.get("object_url") or ""
                if obj.get("object_type") == "image" and "/generated/" in url:
                    urls.append(url)
        # дедуп с сохранением порядка
        return list(dict.fromkeys(urls))

    @staticmethod
    def _raise_if_moderation(messages: list[Any], chat_uuid: str) -> None:
        """Если в сообщениях виден маркер модерации — бросить SyntxModerationError."""
        for m in messages:
            if not isinstance(m, dict):
                continue
            for obj in m.get("message_object", []):
                if not isinstance(obj, dict):
                    continue
                blob = " ".join(str(obj.get(k) or "") for k in ("object_text", "error", "status"))
                meta = obj.get("metadata")
                if isinstance(meta, dict):
                    blob += " " + str(meta.get("error") or "")
                if looks_like_moderation(blob):
                    raise SyntxModerationError(
                        f"материал зарезан модерацией (chat={chat_uuid})",
                        endpoint="/chats/messages",
                    )

    # ====================== internals ======================

    async def _resolve_ref_urls(self, refs: tuple[str, ...]) -> list[str]:
        """Локальные пути → upload → r2-url; готовые url как есть.

        Порядок рефов СОХРАНЯЕТСЯ (для моделей, где первый реф = основной субъект):
        загруженные url подставляются на места локальных путей по позиции.
        """
        local_paths = [r for r in refs if not r.startswith(("http://", "https://"))]
        uploaded = iter(await self.upload_files(local_paths)) if local_paths else iter(())
        resolved: list[str] = []
        for ref in refs:
            if ref.startswith(("http://", "https://")):
                resolved.append(ref)
                continue
            up = next(uploaded, None)
            if up and up.url:
                resolved.append(up.url)
        return resolved

    async def _download_all(self, urls: list[str], base: Path) -> list[Path]:
        out: list[Path] = []
        for i, url in enumerate(urls):
            target = base if len(urls) == 1 else base.with_name(f"{base.stem}_{i + 1}{base.suffix}")
            out.append(await self.download_url(url, target))
        return out

    async def _safe_balance(self) -> float | None:
        try:
            return (await self.get_balance()).tokens
        except SyntxError:
            return None

    async def _log_spend(self, balance_before: float | None, req: GenRequest) -> float | None:
        """Залогировать расход токенов (дельта баланса). Best-effort."""
        balance_after = await self._safe_balance()
        if balance_before is None or balance_after is None:
            return None
        spent = round(balance_before - balance_after, 4)
        if spent <= 0:
            # дельта <= 0 — баланс не успел обновиться / прочитался кривой:
            # не врём в лог «потрачен весь баланс».
            logger.debug(
                "syntx ген %s/%s: расход не определён (дельта %s)",
                req.ai_name,
                req.model_type,
                spent,
            )
            return None
        logger.info(
            "syntx ген %s/%s: потрачено ~%s токенов, баланс %s",
            req.ai_name,
            req.model_type,
            spent,
            balance_after,
        )
        return spent

    async def _get(
        self, path: str, *, params: dict[str, Any] | None = None, api_version: str = "v1"
    ) -> Any:
        return await self._request("GET", path, params=params, api_version=api_version)

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        # POST не ретраим автоматически (generate/upload не идемпотентны).
        return await self._request(
            "POST", path, json=json, params=params, files=files, data=data, retry=False
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        api_version: str = "v1",
        retry: bool = True,
    ) -> Any:
        if self._http is None:
            raise RuntimeError("SyntxClient не запущен: await start()")
        prefix = "/api/" + api_version
        full = prefix + path

        async def _once() -> Any:
            try:
                resp = await self._http.request(
                    method, full, json=json, params=params, files=files, data=data
                )
            except httpx.TransportError as exc:
                raise TemporaryError(
                    f"сетевая ошибка {method} {full}: {exc}", endpoint=path
                ) from exc
            return self._parse(resp, path=path)

        if not retry:
            return await _once()

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
            retry=retry_if_exception_type((TemporaryError, httpx.TransportError)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await _once()
        raise SyntxError(f"retry loop без результата: {method} {full}", endpoint=path)

    @staticmethod
    def _parse(resp: httpx.Response, *, path: str) -> Any:
        if not (200 <= resp.status_code < 300):
            body = resp.text[:500]
            raise classify_http_error(
                resp.status_code,
                f"syntx {resp.status_code} на {path}: {body}",
                endpoint=path,
                response_body=body,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise TemporaryError(
                f"невалидный JSON в ответе {path}: {exc}",
                status_code=resp.status_code,
                endpoint=path,
                response_body=resp.text[:500],
            ) from exc


def _read_upload_file(path: str | Path) -> tuple[str, bytes, str]:
    """Sync: прочитать файл рефа для multipart (вызывается через asyncio.to_thread)."""
    p = Path(path).expanduser()
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return p.name, p.read_bytes(), mime


def _write_bytes(out: Path, data: bytes) -> None:
    """Sync: записать результат на диск (через asyncio.to_thread)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def _safe_settings() -> Any:
    """get_settings() мягко: CLI может работать без полного конфига."""
    try:
        from core.config import get_settings

        return get_settings()
    except Exception:  # noqa: BLE001
        return object()
