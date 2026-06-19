# Дизайн: Telegram — Волна 1 (money-надёжность нотификаций)

Дата: 2026-06-19. Статус: апрувнут пользователем. Часть большого пересмотра формата TG
(целевой формат: DM вместо супергруппы + Mini App-центричность; см. волны 2-4 ниже).

## Контекст и проблема

Аудит TG-канала (35 подтверждённых находок) вскрыл **системный money-пробел**: целый класс
провалов денежных операций не доходит до владельца.

- `meta_api_worker` (авто-стоп, `/pause`, автостарт, create_campaign) и `cabinet_scheduler`
  **вообще без TG-клиента** — любой permanent/exhausted fail только в лог. Реальный случай:
  CR008 — `pause_ad` упал (`code=-2` до фикса), владелец узнал случайно.
- STOP/WARNING-алерт **молча теряется** при кратком TG-outage — нет retry-sweep (осиротевший
  `alert_event`, `dispatch` ищет строго по `scan_id`).
- Несколько воркеров ставят Redis-дедуп **ДО** отправки → при сбое TG алерт потерян на TTL.
- TG-клиент создаётся раз при старте → **stale-токен** при ротации (молчит навсегда).

> Связанный контекст: `_AUTO_STOP_MAX_ATTEMPTS` осознанно снижен 72→**15 (~1ч)** —
> авто-стоп ретраит коротко, затем сдаётся (длинный retry лишь маскировал мёртвый канал;
> его детектит `health_watchdog` probe). Это делает **финальный TG-алерт о провале
> авто-стопа обязательным**: после ~1ч молчаливой сдачи владелец ОБЯЗАН узнать.

## Целевой формат (общий вектор, для контекста)

- **Доставка → личка (DM)**, супергруппу/форум-топики убираем (волна 2). Закрывает класс
  group-ACL багов автоматически.
- **Mini App — центр UX** через cloudflared-туннель (волна 3).
- **Волна 1 (этот спек) — money-надёжность нотификаций.** Не зависит от волн 2-3.

## Решение волны 1

### 1. Единый модуль `core/telegram/worker_notify.py` (новый)

Главная абстракция. Сигнатура:

```python
async def notify_owners(
    engine, redis, *, category: str, text: str,
    dedup_key: str | None = None, dedup_ttl_seconds: int | None = None,
) -> bool
```

Поведение:
- грузит `telegram_config` **на каждом вызове** (свежий токен → чинит ротацию); клиент
  кешируется по `bot_token` (внутренний кеш модуля).
- адресат — **owner-recipient'ы в личке** (`role='owner'`, `revoked_at IS NULL`,
  `chat_id` из их `/start`). НЕ форум-топик. Согласовано с целевым DM-форматом.
- dedup через Redis `SET NX EX` **ТОЛЬКО после успешной отправки** (фикс dedup-before-send).
  Если `dedup_key` уже стоит → ранний `return False` без отправки.
- возвращает `bool` (True = доставлено хотя бы одному owner). Best-effort: исключения
  TG/Redis глотает и логирует, не бросает. Нет owner-получателей → лог + `False`.

Закрывает находки: meta-api-silent, mark_disabled-silent, cabinet-silent,
watchdog-dedup-before-send, stale-tg-config, enable-reco-dedup-before-send.

### 2. Точки подключения к `notify_owners`

| Воркер | Событие | dedup |
|---|---|---|
| `meta_api_worker` | провал `pause_ad`/`bulk pause` (auto-stop + ручной `/pause`) → «❌ не смог отключить fb_ad_id=X, отключи вручную» | `auto_stop_fail:{ad_id}` TTL 1h |
| `meta_api_worker` | `CreateCampaignPartialError` → «осиротевшие id в Meta: …» | по task_id |
| `meta_api_worker` | `TokenInvalidError` → «токен истёк, re-login Vision» | `token_invalid` TTL 1h |
| `meta_api_worker` | долгое зависание ретраев (`attempt_count >= N`) → «авто-стоп ретраит N мин, возможен outage» | `auto_stop_retry:{ad_id}` TTL 30m |
| `observer` (`mark_disabled_when_offline`) | sync OFF→disabled → «помечен disabled, в Meta уже OFF» | `sync_offline_disabled:{ad_id}` TTL 6h |
| `cabinet_scheduler` | `started` → «поднято N объявлений»; `no_owner_ads` → «кабинет НЕ поднят» | по дню |
| `health_watchdog` | мёртвый воркер (уже есть, но dedup-before-send) → перевести на `notify_owners` | существующий |
| `enable_reco` | `mark_recommended` — перенести ПОСЛЕ успешной отправки | существующий |

`cabinet_scheduler` дополнительно: перенести `done`-маркер ПОСЛЕ успешного резолва, чтобы
`no_owner_ads` ретраился в окне (сейчас маркер до возврата → catch-up не срабатывает).

### 3. Точечные фиксы канала алертов (не через `notify_owners`)

- **retry-sweep осиротевших** (`core/telegram/alert_dispatcher.py`): отдельный запрос
  `alert_events` за 24h без соответствующего `telegram_message_refs`
  (`ad_id + incident_key + stream_kind`), вызывать в начале каждого dispatcher-цикла
  независимо от счётчиков. Чинит потерю STOP/WARNING при TG-outage.
- **`client.py` 5xx-retry**: 502/503/504 → 1-2 ретрая с backoff (сейчас только 429).
- **`redis_client` в `dispatch_pending_alerts`**: пробросить из `_run_account_scan`
  (одна строка) → чинит realtime publish `fb_agent:alert:created` фронту.

### 4. Предпосылка (онбординг)

Нотификации работают, когда есть ≥1 owner-recipient в личке (сейчас 0). Разработка+тесты
не требуют этого; **живая верификация — после** того как пользователь один раз пришлёт боту
`/start <invite>` (invite-код генерируется отдельно).

## Границы (НЕ в волне 1)

- Дайджест-спенд per-day CTE (money, но не «молчащий канал») — отдельная задача.
- Снос супергруппы/форум-топиков, перевод основного алерт-канала на DM — **волна 2**.
- dead-code снуза, stale docstrings, единый стиль карточек — **волна 4**.

## Тестирование

- Unit `worker_notify`: dedup-after-send (сбой отправки → ключ НЕ ставится), fresh-config
  (повторная загрузка токена), no-recipients → no-op `False`, успех → `True` + dedup стоит.
- Unit на каждую точку подключения: мок `notify_owners`, проверка вызова с нужной категорией/
  dedup при провале/событии.
- Integration: retry-sweep ресендит осиротевший `alert_event`, не дублирует уже доставленный.
- 5xx-retry unit; `redis_client`-проброс unit.

## Метрика готовности

Все money/high находки канала нотификаций закрыты; unit+integration зелёные; ruff чисто.
Живой прогон провала мутации (или симуляция) → owner получает DM (после онбординга).
