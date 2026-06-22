# Findings: AI/MCP/Creatives/AdLib/Syntx/Crypto/Config

Дата аудита: 2026-06-22  
Ревьювер: deep-audit subagent (claude-sonnet-4-6)

---

## HIGH — Partition full-scan × 3 в Ad Library pipeline

**Файлы:**
- `core/ad_library/media.py:188`
- `core/ad_library/enricher.py:129`
- `core/ad_library/tier_ranker.py:110`

**Проблема.** Таблица `ad_library_snapshot` партиционирована `RANGE (scanned_at)`. Все три модуля фильтруют строки только по `scan_id = :sid` — без колонки-ключа партиции `scanned_at`. Postgres вынужден сканировать все месячные партиции при каждом `/spy`-запросе. За 14 дней retention (~5–10 партиций) и росте кол-ва сканов это деградирует от миллисекунд до секунд на каждый шаг pipeline.

**Почему важно.** `/spy <slot> <country>` — интерактивная команда в Telegram-боте, пользователь ждёт ответа. Постепенная деградация незаметна до критического порога.

**Фикс.** При сохранении scan-записи (`INSERT ad_library_scan`) читать `started_at`. Передавать `started_at` в helper-функции и добавлять:

```sql
AND s.scanned_at >= :started_at - interval '1 minute'
AND s.scanned_at <= :started_at + interval '1 hour'
```

Или JOIN через `ad_library_scan.started_at` внутри запроса. Тогда Postgres pruning уберёт все лишние партиции.

---

## MID — INCR + EXPIRE в rate-limiter нe атомарны

**Файл:** `core/ai_assistant/tools/_ratelimit.py:93–96`

```python
current = await redis_client.incr(key)      # команда 1
if current == 1:
    await redis_client.expire(key, _DEFAULT_TTL_SECONDS)  # команда 2
```

**Проблема.** Если процесс упадёт (OOM, SIGKILL) между двумя командами, ключ остаётся без TTL и никогда не истекает — rate-limit для этого `client_key` заблокирован навсегда (или до следующей ротации Redis). Вероятность не нулевая на продакшн-хосте с ограниченной памятью и OOM Killer.

**Влияние.** AI-инструменты через MCP или `/ai/analyze` вернут `RateLimitExceeded` вечно, без возможности восстановления без ручного `DEL` ключа в Redis.

**Фикс.** Lua script (атомарен):

```python
_INCR_EXPIRE_SCRIPT = """
local cur = redis.call('INCR', KEYS[1])
if cur == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return cur
"""
current = await redis_client.eval(_INCR_EXPIRE_SCRIPT, 1, key, ttl)
```

Или: после `incr` всегда проверять `TTL == -1` и принудительно ставить expire (идемпотентно, небольшая overhead).

---

## MID — env_file = ".env" — относительный путь, зависит от CWD

**Файл:** `core/config.py:185`

```python
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"   # абсолютный путь — НЕ используется
model_config = {"env_file": ".env", "extra": "ignore"}      # относительный — используется
```

**Проблема.** Если какой-либо из 12 воркеров (или Docker entrypoint) запускается из директории, отличной от корня проекта, `.env` не найдётся — все настройки падают на дефолты. `encryption_key`, `telegram_bot_token`, `api_key`, `adsetpro_postback_secret` будут пустыми строками. `model_validator` пишет warning в лог, но не останавливает старт — процесс стартует в «тихом» degraded-режиме.

**Фикс:**

```python
model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}
```

`_ENV_FILE` уже вычислен корректно через `__file__`, достаточно его использовать.

---

## MID — gRPC search_ads() без asyncio timeout в Ad Library scanner

**Файл:** `core/ad_library/scanner.py` (строка вызова `client.search_ads()`)

**Проблема.** `client.search_ads()` — gRPC-стриминговый вызов к browser-agent — не обёрнут в `asyncio.wait_for`. Если browser-agent завис (сетевой timeout, Vision session freeze), coroutine блокируется навсегда. Telegram-поллер, вызывающий `/spy`, ждёт вечно и не обрабатывает другие сообщения (или создаёт накапливающийся backlog).

**Фикс:**

```python
result = await asyncio.wait_for(client.search_ads(request), timeout=120.0)
```

С обработкой `asyncio.TimeoutError` → UPDATE scan status=failed + ответ пользователю.

---

## MID — ffmpeg subprocess без timeout в video_uniquifier

**Файл:** `core/creatives/video_uniquifier.py` (`_run_tool`, вызов `asyncio.create_subprocess_exec`)

**Проблема.** `await process.communicate()` без timeout. Повреждённое входное видео может заставить ffmpeg зависнуть или работать часы. Запрос к `/tools/creative-uniquify` (API endpoint) будет висеть, удерживая HTTP-соединение и asyncio event loop task.

**Фикс:**

```python
try:
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300.0)
except asyncio.TimeoutError:
    process.kill()
    raise VideoUniquifyError(f"{tool}: timeout (>300s), файл повреждён?")
```

---

## MID — decrypt() возвращает "" при InvalidToken (тихая деградация)

**Файл:** `core/crypto.py:341` (функция `decrypt`)

```python
except InvalidToken:
    return ""
```

**Проблема.** Если Vision-токен или Telegram-токен зашифрован старым ключом (после ротации без `rotate_encryption_key`), `decrypt()` вернёт пустую строку. Вызывающий код получит `""` как валидный токен, попробует подключиться, получит HTTP 401/403 от внешнего сервиса, и весь autostart/observer будет молчать о причине.

**Влияние.** Может выглядеть как «Telegram-бот не отвечает» или «Vision не коннектится» без явной ошибки про ключ.

**Фикс.** Логировать `CRITICAL` с именем поля (без значения!) и перебрасывать или возвращать специальный sentinel:

```python
except InvalidToken:
    logger.critical("decrypt: InvalidToken для %s — ключ не совпадает с зашифрованным", context)
    raise CryptoDecryptError(f"Неверный ключ для {context}")
```

---

## LOW — Singleton AIClient кэширует API keys навсегда

**Файл:** `core/ai_assistant/client.py:74–108`

**Проблема.** `_client_singleton` создаётся при первом вызове `get_ai_client()` и захватывает `Settings` того момента. Если `ANTHROPIC_API_KEY` ротируется в `.env` без перезапуска процесса — старый ключ используется до рестарта. Не критично при текущей архитектуре (все воркеры рестартуются через `run.sh`), но создаёт скрытое поведение при будущей key rotation.

**Фикс.** Нет срочности — документировать, что ротация ключей требует перезапуска воркеров.

---

## LOW — TOOL_SCHEMAS = импорт-снимок (не используется динамически)

**Файл:** `core/ai_assistant/tools/__init__.py:86`

```python
TOOL_SCHEMAS: list[dict[str, Any]] = GLOBAL_REGISTRY.schemas()
```

**Проблема.** Снимок берётся в момент импорта модуля. `ChatSession.ask()` корректно вызывает `GLOBAL_REGISTRY.schemas()` динамически — этот константный снимок не используется. Если кто-то снаружи импортирует `TOOL_SCHEMAS` и ожидает актуального состояния, он получит стейл-данные. Тесты, регистрирующие mock-tools после импорта, также не увидят изменений через эту переменную.

**Фикс.** Переименовать в `_INITIAL_TOOL_SCHEMAS` или удалить. Либо заменить на `@property` в специальном объекте.

---

## LOW — spec_from_dict не проверяет path containment для PNG-оверлеев

**Файл:** `core/creatives/video_overlay.py:278–290`

```python
file = Path(raw["file"])
pngs.append(PngOverlay(
    file=file if file.is_absolute() else base / file,
    ...
))
```

**Проблема.** Нет проверки, что `file` находится внутри `base_dir`. Путь типа `../../etc/passwd` технически пройдёт (хотя ffmpeg попытается читать его как PNG и упадёт). Сейчас `spec_from_dict` вызывается только из скриптов с доверенным вводом, не из HTTP endpoint. Актуально при будущей экспозиции `overlay_video` через API.

**Фикс.** Добавить проверку при наличии `base_dir`:

```python
resolved = (base / file).resolve()
if base_dir and not resolved.is_relative_to(base_dir.resolve()):
    raise OverlayValidationError(f"PNG path outside base_dir: {file}")
```

---

## LOW — httpx.AsyncClient создаётся per-call в AI-провайдерах

**Файл:** `core/ai_assistant/providers.py`

**Проблема.** `AnthropicProvider.chat()` и `OpenAIProvider.chat()` создают новый `httpx.AsyncClient` на каждый вызов. Каждый раз — новое TCP-соединение (и TLS handshake) к AI-proxy. Для диалога с несколькими tool-rounds это умножается. Не критично при текущем трафике (30/hour лимит), но неэффективно.

**Фикс.** Использовать клиент как instance variable `Provider.__init__` с lifecycle (`.aclose()` при shutdown) или пул через `httpx.AsyncHTTPTransport(limits=...)`.

---

## Итог по уровням

| Severity | Кол-во | Краткое описание |
|----------|--------|-----------------|
| HIGH     | 3      | Partition full-scan в media/enricher/tier_ranker |
| MID      | 5      | INCR+EXPIRE non-atomic; env_file CWD-зависимость; gRPC без timeout; ffmpeg без timeout; decrypt silent "" |
| LOW      | 3      | AIClient stale keys; TOOL_SCHEMAS stale snapshot; spec_from_dict path traversal |

**Чистые зоны:** MCP read-only enforcement (двойная защита), DRAFT_REQUIRED ACL, alert_dispatcher pre-claim sentinel, color whitelist в video_overlay, path traversal в folder_opener, partition key в get_recent_alerts (MCP resources), Fernet rotate_encryption_key транзакция.
