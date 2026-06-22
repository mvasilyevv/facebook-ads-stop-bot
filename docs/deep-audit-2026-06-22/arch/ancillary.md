# Архитектура подсистемы: AI/MCP/Creatives/AdLib/Syntx/Crypto/Config

## Назначение

Вспомогательные подсистемы, которые не управляют основным FSM автостопа, но обеспечивают:
- **AI-ассистент** — read-only аналитика + write-черновики (через TG-подтверждение) поверх LLM-провайдеров;
- **MCP-сервер** — stdio-транспорт для Claude Desktop / Cursor, только read-only;
- **Creatives** — уникализация изображений (PIL) и видео (ffmpeg) для FB-рекламы;
- **Ad Library** — pipeline разведки конкурентов через Meta Ad Library (gRPC → browser-agent);
- **Crypto** — Fernet-шифрование токенов в БД, ротация ключа;
- **Config** — pydantic-settings синглтон `.env → Settings`;
- **Pubsub/Control** — Redis pub/sub шина между воркерами и API.

---

## Компоненты

### core/ai_assistant/

| Файл | Роль |
|------|------|
| `client.py` | `AIClient` — primary Anthropic + fallback OpenAI, синглтон `_client_singleton` |
| `providers.py` | `AnthropicProvider` / `OpenAIProvider` — httpx-запросы, перевод Anthropic-формата в OpenAI |
| `chat.py` | `ChatSession` — один request/response цикл с multi-round tool-use, двойной rate-limit |
| `tools/__init__.py` | Side-effect импорт subpackage'ей → заполнение `GLOBAL_REGISTRY`; `execute_tool`, `check_rate_limit` |
| `tools/registry.py` | `ToolRegistry` + `GLOBAL_REGISTRY` (singleton dict) |
| `tools/base.py` | `RiskLevel`, `ToolContext`, `ToolHandler` (Protocol), `ToolError` |
| `tools/_ratelimit.py` | Redis INCR/EXPIRE + in-memory fallback (5/60s) |
| `tools/ops/*` | `get_active_offers`, `get_recent_alerts`, `get_worker_health`, `get_disable_tasks_status` — READ_ONLY БД/Redis |
| `tools/meta/*` | `find_ads`, `get_insights`, `get_account_health`, `get_offer_performance`, `get_competitor_patterns` — READ_ONLY Marketing API |
| `tools/trackers/*` | `get_tracker_stats` — READ_ONLY tracker_aggregate |
| `tools/drafts/*` | `request_bulk_pause`, `request_budget_change`, `request_clone_campaign`, `request_create_campaign` — DRAFT_REQUIRED, создают task_queue.draft |
| `tools/creative/*` | `analyze_creative`, `generate_ad_copy` — CREATIVE (LLM без mutations) |

### apps/mcp_server/

| Файл | Роль |
|------|------|
| `main.py` | `build_server()` — mcp.Server stdio, `list_tools` фильтрует DRAFT_REQUIRED, `call_tool` блокирует их в runtime |
| `context.py` | `MCPContextManager` — async-context: engine + redis + optional MetaApiClient |
| `tool_adapter.py` | `adapt_to_mcp_tool()` — конвертация ToolHandler → mcp.types.Tool (input_schema, camelCase) |
| `resources.py` | 4 ресурса: offers, recent-alerts, workers-health, schema-overview |

### core/creatives/

| Файл | Роль |
|------|------|
| `service.py` | `uniquify_creatives()` — оркестрация: validate → temp_dir → PIL per copy → rename (атомарный) |
| `uniquifier.py` | `uniquify_image_bytes()` — PIL: exif_transpose, micro-crop, tone_shift, noise → JPEG |
| `video_uniquifier.py` | `uniquify_videos()` — ffprobe + ffmpeg: crop+scale+eq+noise+setpts, seeded random |
| `video_overlay.py` | `overlay_video()` — drawtext + PNG overlay через filter_complex |
| `folder_opener.py` | `open_generated_folder()` — path-traversal защита через `.is_relative_to(root)` |

### core/ad_library/

| Файл | Роль |
|------|------|
| `scanner.py` | `run_scan()` — gRPC AdLibraryClient → INSERT ad_library_scan + _persist_ads |
| `media.py` | `download_for_scan()` — httpx скачивание + sha256 дедуп → ad_library_media |
| `enricher.py` | `enrich_scan()` — heuristic hook/cta/tone → UPDATE ad_library_ad.ai_summary |
| `tier_ranker.py` | `rank_scan()` — pure compute_tier(days_running, page_history, cluster_size) + INSERT ad_library_tier + S-tier → winner_archive |
| `report.py` | `build_report()` — агрегация tier + vertical breakdown + markdown → ad_library_report |
| `pipeline.py` | `run_pipeline()` — оркестратор: scan → media → enrich → rank → report |
| `spy_handler.py` | `execute_spy()` → `parse_spy_args()` + `run_pipeline()` |
| `classifier.py` | `score_relevance_to_slot()`, `extract_ad_text()` — pure функции |

### core/crypto.py

Fernet-шифрование токенов (telegram_config.bot_token_encrypted, vision_config.x_token_encrypted, adsetpro_credentials.*). Ленивый синглтон `_fernet`. При первом запуске без ключа — генерирует ENCRYPTION_KEY и пишет в `.env`. `rotate_encryption_key()` создаёт отдельный AsyncEngine, перешифровывает всё в одной транзакции.

### core/config.py

`Settings(BaseSettings)` с `env_file=".env"` (относительный путь от CWD). Ленивый синглтон `_settings`. `_ENV_FILE` объявлен как абсолютный путь, но в `model_config` **не используется** (используется относительный ".env").

### core/pubsub.py / core/control/pubsub_listener.py

- `core/pubsub.py` — `RedisPubSub`: publish-side + subscribe async-генератор (два разных соединения). Каналы: `fb_agent:scan:finished`, `fb_agent:alert:created`, `fb_agent:task:changed`, `fb_agent:health:updated`.
- `core/control/pubsub_listener.py` — `RedisPubSubListener`: polling (timeout=0 + sleep POLL_INTERVAL=0.05s), channel → handlers диспетч. Используется observer_worker для получения `scan-now` / `cabinet_day` / `restart` сигналов от API.

---

## Последовательности вызовов

### AI tool call (HTTP /ai/analyze — no tools)
```
POST /ai/analyze
  → _extract_client_key() → check_and_increment(Redis, namespace="analyze")
  → ChatSession(allow_tools=False).ask(history)
    → get_rate_limiter().hit(client_key)          # in-memory 30/hr
    → check_rate_limit(ctx)                       # Redis 30/hr (если redis есть)
    → ai.chat(messages, tools=None)               # Anthropic или OpenAI
  → Redis cache SET ai:cache:analyze:{block}:{scope} TTL 600s
```

### AI tool call (MCP stdio)
```
Claude Desktop → MCP list_tools → [READ_ONLY + CREATIVE только, DRAFT_REQUIRED отфильтрованы]
Claude Desktop → MCP call_tool("get_recent_alerts", {hours:24})
  → handler.risk_level != DRAFT_REQUIRED check
  → check_and_increment(Redis, client_key="mcp:claude-desktop")
  → execute_tool → GLOBAL_REGISTRY.execute → GetRecentAlertsTool.run(ctx, args)
    → engine.connect() → SELECT alert_events WHERE created_at >= NOW()-...
```

### Ad Library pipeline (/spy команда)
```
TG /spy <slot> <country>
  → parse_spy_args() → SpyRequest
  → execute_spy() → run_pipeline(engine, slot, country)
    → run_scan()
      → INSERT ad_library_scan (status=running)
      → AdLibraryClient.start() + .search_ads() [gRPC → browser-agent → Meta GraphQL]
      → _persist_ads(): FOR EACH ad: UPSERT ad_library_ad + INSERT ad_library_snapshot
      → UPDATE ad_library_scan (status=done/failed)
    → download_for_scan()
      → SELECT raw_json FROM ad_library_snapshot WHERE scan_id=:sid  ← FULL SCAN (нет scanned_at)
      → httpx.get(url) → sha256 dedup → write_bytes → INSERT ad_library_media
    → enrich_scan()
      → SELECT ... FROM ad_library_snapshot s WHERE s.scan_id=:sid  ← FULL SCAN
      → heuristic → UPDATE ad_library_ad
    → rank_scan()
      → SELECT FROM ad_library_snapshot WHERE scan_id=:sid  ← FULL SCAN
      → compute_tier() per ad → INSERT ad_library_tier
      → S-tier → INSERT ad_library_winner_archive
    → build_report()
      → SELECT FROM ad_library_snapshot WHERE scan_id=:sid  ← через JOIN
      → INSERT ad_library_report
  → format_short_summary() → TG send
```

### Creatives уникализация (изображения)
```
POST /tools/creative-uniquify (multipart)
  → uniquify_creatives(offer_name, copies, creatives)
    → _validate_inputs()
    → tmp_dir = root / ".tmp_<iter>_<uuid>"
    → FOR copy_index in 1..N:
        FOR creative in creatives:
          → uniquify_image_bytes() [PIL: micro_resample, tone_shift, noise → JPEG]
          → write_bytes(copy_dir/name.jpeg)
    → temp_dir.rename(iteration_dir)  # атомарная операция
```

### Ротация Fernet ключа
```
rotate_encryption_key(old_key, new_key)
  → save old_key → .encryption_key.old
  → create_async_engine(settings.database_url)
  → async with engine.begin():
      SELECT telegram_config → decrypt(old) → encrypt(new) → UPDATE
      SELECT vision_config → decrypt(old) → encrypt(new) → UPDATE
      SELECT adsetpro_credentials → decrypt(old) → encrypt(new) → UPDATE
  → engine.dispose()
```

---

## Зависимости

**Что зависит от этих подсистем:**
- `apps/api/routers/v1/ai_analyze.py` → `core/ai_assistant/chat.py`
- `apps/api/routers/v1/tools.py` → `core/creatives/service.py`
- `apps/telegram_poller/` → AI-ассистент удалён (`/ask` снесён)
- `apps/mcp_server/` → `core/ai_assistant/tools/*` (через GLOBAL_REGISTRY)
- `core/ad_library/scanner.py` → `clients/python_grpc/ad_library_client.py` → browser-agent gRPC

**От чего зависит подсистема:**
- `core/config.py` ← все воркеры (синглтон `get_settings()`)
- `core/crypto.py` ← telegram_poller, vision startup (decrypt token из БД)
- `core/pubsub.py` ← API (publish scan-now), observer_worker (subscribe)
- `GLOBAL_REGISTRY` ← заполняется side-effect при импорте `core.ai_assistant.tools` → все subpackage регистрируют свои классы

---

## Потоки данных

| Источник | Структура | Куда |
|----------|-----------|------|
| Ad Library scan | `ScanResult` (dataclass) | `ad_library_scan`, `ad_library_ad`, `ad_library_snapshot` (partitioned) |
| Media download | bytes + sha256 | `~/data/ad_library_media/`, `ad_library_media` |
| Tier ranking | `TierEntry` | `ad_library_tier`, `ad_library_winner_archive` |
| AI tool result | str | LLM tool_result block → history |
| DRAFT tool | `MetaMutationPayload` | `task_queue` (status='draft', created_by_chat_id) |
| Fernet encrypt | plaintext token → ciphertext | `telegram_config.bot_token_encrypted`, `vision_config.x_token_encrypted` |
| Creative uniquify | bytes (image/video) | `~/Documents/FB_Agent_Creo/<iter>/<copy>/` |
| Redis pubsub | JSON payload | observer_worker (trigger scan-now, cabinet_day) |
| Rate-limit | INCR counter | Redis `ai:ratelimit:{ns}:{key}` TTL 3600s |

---

## Внешние взаимодействия

- **Anthropic API** (proxy `api.claudehub.fun/v1`) — httpx POST `/messages` с API-Key header
- **OpenAI-compatible API** (proxy `gateway.nekocode.app`) — httpx POST `/chat/completions`
- **browser-agent gRPC** (localhost:50051) — AdLibraryClient, MetaApiClient (используется meta/* tools в MCP)
- **Redis** — rate-limit ключи `ai:ratelimit:*`, worker heartbeats `worker:heartbeat:*`, pubsub каналы
- **Postgres** — все ORM-запросы через SQLAlchemy AsyncEngine
- **ffmpeg/ffprobe** — subprocess вызовы из `video_uniquifier.py` и `video_overlay.py`
- **PIL (Pillow)** — in-process обработка изображений в `uniquifier.py`
- **Meta Ad Library** (через browser-agent) — GraphQL запросы из `AdLibraryClient.search_ads()`

---

## Инварианты и контракты

1. **GLOBAL_REGISTRY** заполняется один раз при импорте `core.ai_assistant.tools`. Порядок регистрации — алфавитный (ops, meta, drafts, creative, trackers). После заполнения реестр не должен изменяться в проде (только `unregister` в тестах).

2. **MCP read-only enforcement** — двойная защита: `list_tools` не экспонирует DRAFT_REQUIRED, `call_tool` проверяет risk_level повторно. Даже если LLM угадает имя draft-tool, второй check его заблокирует.

3. **DRAFT_REQUIRED созданные черновики** — `created_by_chat_id=None` при вызове через MCP/HTTP. Подтвердить их через `approve_draft_task` может только admin (`admin_override=True`), обычный owner не может (нет его `chat_id` в записи).

4. **CreativeService атомарность** — запись сначала в `temp_dir`, потом `rename → iteration_dir`. Сбой в середине → `finally: rmtree(temp_dir)`. Уже существующий `iteration_dir` у image-версии **сносится** перед rename; у video-версии — `CreativeValidationError` (разное поведение).

5. **Ad Library pipeline** — scan_id (UUID) является ключом связи между всеми таблицами (snapshot, tier, media), но `ad_library_snapshot` партиционирована по `scanned_at`, и pipeline-запросы фильтруют только по `scan_id` — partition pruning не работает.

6. **Fernet ключ** — синглтон `_fernet`, инициализируется при первом вызове `encrypt()`/`decrypt()`. Ключ верифицируется по `ENCRYPTION_KEY_VERIFY` при каждом запуске. Если verify отсутствует — верификация пропускается с предупреждением (старые инсталляции).

7. **Config CWD зависимость** — `model_config = {"env_file": ".env"}` использует относительный путь, т.е. `.env` ищется относительно текущей рабочей директории процесса, а не корня проекта.
