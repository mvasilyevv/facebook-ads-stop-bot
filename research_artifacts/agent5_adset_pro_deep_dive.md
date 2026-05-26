# Adset.Pro — глубокое исследование как канала интеграции для FB Stop Bot

**Дата:** 2026-05-25
**Автор отчёта:** агент-исследователь (Claude)
**Цель:** оценить, можем ли мы (FB Stop Bot) использовать adset.pro как источник данных или способ обойти ограничения Meta Marketing API для гемблинг-вертикали.

> **Ключевой вывод заранее:** adset.pro **никогда** не подключается к Facebook на стороне рекламного кабинета. Это **tracker-side система** с собственным MCP-сервером, отдающим статистику исключительно по событиям, которые сам трекер собирает (клики на трекинговые ссылки + постбэки от партнёрок). Никаких Marketing API, OAuth-к Facebook, или способа управлять рекламой через adset.pro **не существует**. Меняет управление FB на стороне Vision-браузера остаётся единственным путём.

---

## 1. Что такое adset.pro в деталях

**Adset.Pro** — облачная мультиплатформа для арбитражника, объединяющая в одной экосистеме:
- **трекер** (cloud-based, без self-hosted);
- **конструктор PWA-приложений** (no-code, с пуш-уведомлениями и собственным хостингом);
- **систему push-уведомлений** (web push + post-click attribution);
- **встроенный CRM/командное управление** (RBAC: Buyer / Team Lead / Tech);
- **публичный HTTP API + MCP-сервер** (запущен 19–20 мая 2026).

Год основания — **2023** (по карточке на TribunAff [tribunaff.com/services/adset-pro/](https://tribunaff.com/services/adset-pro/)).

Аудитория — **CIS-арбитражники**, преимущественно в вертикалях **iGaming, betting, nutra, sweepstakes, dating, mobile-offers**. Гео работы — Tier-1/Tier-2 (Европа, LATAM, Азия). Документация полностью на русском (`/guide_ru/...`), английская локализация присутствует, но второстепенна.

**Юрисдикция / юрлицо** — намеренно не раскрывается. В Terms & Conditions ([adset.pro/docs/terms-and-conditions](https://adset.pro/docs/terms-and-conditions)) оператор называет себя обезличенно «Operator» и заявляет: *"Disputes... may be brought in the competent courts of the Operator's jurisdiction of incorporation/registration"*. Имя юрлица отсутствует, в Privacy Policy — тоже. Это типовая практика для CIS-сервисов, обслуживающих gray-сегмент.

**Авторы документации** (видны в meta-полях статей):
- *Denis* — практически вся технико-продуктовая документация (трекер, MCP, CAPI, API);
- *Alexander* — экономические/тарифные страницы (тарифные планы, ресурсы).

**Связь с проектом `adop.team`** — то же приложение, sitemap-docs.xml дублируется ([adop.team/sitemap-docs.xml](https://adop.team/sitemap-docs.xml)). Похоже на B2B-/white-label-бренд того же оператора.

### Тарификация

Источник: [`/guide_ru/tarifnye-plany---obzor`](https://adset.pro/guide_ru/tarifnye-plany---obzor) и [cpa.rip обзор](https://cpa.rip/services/adset-pro-treker/).

| Тариф | Цена | Базовая модель |
|---|---|---|
| **Starter** | $0/мес | pay-as-you-go: $0.10 / 1 000 событий, $0.06 / PWA-установку, $10 / активный участник |
| **Buying** (для социального трафика) | $99/мес | 100 000 событий вкл., далее $0.07 / 1 000 |
| **Business** (для социального трафика) | $399/мес | 200 000 событий вкл., далее $0.06 / 1 000 |
| **Business** (для рекламных сетей) | $399/мес | 400 000 событий вкл., далее $0.05 / 1 000 |
| **Enterprise** | $999/мес | (по обзору CPA.RIP) — 50 000 PWA-установок, VIP-поддержка, private chat |

**MCP/API доступны на ВСЕХ тарифах**, включая Starter — отдельной платы за API/MCP нет. Лимиты — мягкие (см. раздел 3).

---

## 2. КАК ИМЕННО adset.pro подключается к Facebook (самый важный вопрос)

**Ответ:** adset.pro **не подключается** к Facebook со стороны Ads Manager / Marketing API вообще. У них **нет** ни одного из четырёх возможных каналов в полноценном виде — ни OAuth-Login конкретного пользователя в FB, ни своего Meta App со Standard Access, ни браузерной автоматизации, ни ручного ввода данных из FB.

Что есть **в реальности** — четыре односторонних механизма, каждый из которых работает **только в сторону FROM/TO tracker** и **не даёт чтения** данных рекламного кабинета:

### 2.1 Conversions API (CAPI) — events FROM tracker TO Facebook

Источник: [`/guide_ru/facebook-pixel-conversions-api`](https://adset.pro/guide_ru/facebook-pixel-conversions-api).

> *«Facebook Conversions API (CAPI) — это server-to-server интеграция, которая позволяет отправлять события конверсий **напрямую из вашего сервера в Facebook**.»*

- Пользователь сам идёт в Facebook Events Manager, получает `Pixel ID` (16 цифр) и **Access Token пикселя** (`EAABsbCS...`).
- Эти данные он копирует в раздел Pixels в adset.pro.
- Adset.pro затем при каждой конверсии (`pwa_install` → Lead, `hold` → CompleteRegistration, `accept` → Purchase) делает HTTP POST в `graph.facebook.com/.../events`.
- Передаются `client_ip_address`, `client_user_agent`, `fbc` (Click ID), `event_id`, валюта/сумма.

**Это однонаправленно: tracker → FB.** Adset.pro не получает в ответ ничего, кроме статуса доставки event'а. Никакого чтения campaign/ad данных тут нет.

### 2.2 Sources & Postback — events FROM ad source URL TO tracker

Источник: [`/guide_ru/istochniki-trafika-sources`](https://adset.pro/guide_ru/istochniki-trafika-sources).

- Пользователь генерирует в adset.pro трекинговый URL вида `https://yourdomain.com/track/{campaign_id}?fbclid={{fbclid}}&utm_campaign={{campaign.name}}&sub4={{campaign.id}}&sub5={{adset.id}}&sub6={{ad.id}}` и вставляет его в FB Ads Manager как URL объявления.
- Когда FB отрабатывает клик, он автоматически подставляет свои макросы (`{{campaign.id}}`, `{{adset.id}}`, `{{ad.id}}`, `{{fbclid}}`).
- Adset.pro **узнаёт о campaign/adset/ad ID только потому, что пользователь сам зашил макросы в URL** — не из API.

Это видно прямо в шаблоне Telegram-уведомления ([`/guide_ru/telegram-bot----otbivka-konversiy-v-telegram-kanal`](https://adset.pro/guide_ru/telegram-bot----otbivka-konversiy-v-telegram-kanal)):

```
FB Campaign:{event.ext_sub4} ({event.ext_utm_content})
FB Adset:{event.ext_sub5} ({event.ext_utm_term})
FB Ad:{event.ext_sub6} ({event.ext_sub1})
```

То есть **«FB Campaign» — это просто `sub4`-параметр, который арбитражник сам положил в URL**.

### 2.3 Чего НЕТ

Я проверил всю документацию (54 страницы в `sitemap-docs.xml`, см. [adset.pro/sitemap-docs.xml](https://adset.pro/sitemap-docs.xml)) и не нашёл:
- **никакого OAuth-логина в Facebook** (нет шага «Connect Facebook Account»);
- **никакого упоминания Marketing API / `ads_read` / `ads_management` / `business_management` scope**;
- **никакого Meta App ID / App Review статуса** (adset.pro не выступает Meta-приложением);
- **никакой возможности pause/resume/edit/create campaigns** на FB через UI или MCP;
- **никаких функций браузерной автоматизации** (нет упоминаний Playwright/Selenium/anti-detect внутри adset.pro);
- **никакой синхронизации статусов объявлений** между adset.pro и FB.

### 2.4 Вывод по подключению FB

Adset.pro — это **закрытая tracker-вселенная**. Все данные о Facebook, которые в ней есть, попадают туда **исключительно через URL-макросы и пиксельные события**, которые сам пользователь настроил. Это категорически **не альтернатива** Marketing API.

**Для FB Stop Bot это означает:** через adset.pro мы не можем ни читать состояние объявлений (`effective_status`, `delivery_info`), ни менять (`POST /act_<id>/ads`). Если нам нужны метаданные/контроль рекламы в FB — единственный путь по-прежнему **либо Vision браузер (что мы и делаем), либо Marketing API через App Review**.

---

## 3. Что отдаёт MCP-сервер adset.pro

Источник: [`/guide_ru/mcp-servis-adset`](https://adset.pro/guide_ru/mcp-servis-adset) (опубликовано 2026-05-19, обновлено 2026-05-20), плюс мой прямой пробинг `https://adset.pro/.well-known/*`.

### 3.1 Базовый URL и транспорт

```
URL:        https://adset.pro/mcp
Transport:  HTTP streaming (Streamable HTTP MCP)
Methods:    GET и POST (оба требуют Bearer-токен)
```

### 3.2 Tool list (read-only, ровно три tool'а)

| Tool | Скоуп | Назначение |
|---|---|---|
| `query_stats` | `stats:query` | Запрос агрегированной статистики с пресетами времени, метриками, группировками, фильтрами (до 1 000 строк за запрос) |
| `get_metadata` | `stats:meta` | Каталог доступных метрик, групп, фильтров |
| `export_csv` | `stats:export` | Экспорт выборки в CSV без пагинации (мягкий лимит 100 000 строк) |

> **`query_stats` и `export_csv` принимают тот же DTO**, что и REST-эндпойнт `/api/stats/query` (см. раздел 4). То есть MCP — это тонкая обёртка над публичным HTTP API.

Транскрипт видео упоминает `get_campaigns` и `generate_report` — этого в актуальной документации **нет**. Скорее всего автор видео обобщил три tool'а под понятные имена, либо часть фич была переименована.

### 3.3 Аутентификация — два пути

**Путь A: OAuth 2.1 + PKCE (для ChatGPT)**

- ChatGPT обнаруживает OAuth-сервер по RFC 9728 / 8414 metadata.
- Я лично проверил оба endpoint'а — они отвечают:
  - `GET https://adset.pro/.well-known/oauth-protected-resource` → `{"resource":"https://adset.pro/mcp","authorization_servers":["https://adset.pro"],"scopes_supported":["stats:query","stats:meta","stats:export","stats:reports","entities:read","entities:read:full","entities:resolve","api:stats","api:stats:export","api:stats:meta"],"bearer_methods_supported":["header"]}`.
  - `GET https://adset.pro/.well-known/oauth-authorization-server` → authorization_endpoint `https://adset.pro/oauth/authorize`, token_endpoint `https://adset.pro/api/oauth/token`, registration_endpoint `https://adset.pro/api/oauth/register`, grants `authorization_code`+`refresh_token`, code_challenge_methods `S256`+`plain`.
- **Dynamic Client Registration (RFC 7591) включена и работает без модерации.** Я провёл POST на `/api/oauth/register` с тестовыми redirect_uri и получил живой `client_id` + `client_secret` (oacs_…) без всякого approval. То есть **порог входа для third-party интеграции — ноль**.

**Путь B: Personal Access Token (для Claude Desktop / Cursor / Claude Code)**

- Пользователь идёт в UI Adset.pro → Профиль → MCP Keys → Create API Key, выбирает scopes, опционально срок действия.
- Получает токен формата `mcp_a1b2c3d4...` (~64 символа), показывается **один раз**.
- Подключается в `claude_desktop_config.json` как:
  ```json
  {
    "mcpServers": {
      "adset": {
        "type": "http",
        "url": "https://adset.pro/mcp",
        "headers": { "Authorization": "Bearer mcp_..." }
      }
    }
  }
  ```
- В Claude Code CLI: `claude mcp add adset --transport http --url https://adset.pro/mcp --header "Authorization: Bearer mcp_..."`.

### 3.4 Безопасность и ограничения

- **Read-only**. Все три tool'а — чтение статистики. Никаких write-операций (создать кампанию, изменить ставку, удалить flow) MCP не предоставляет. В видео это и заявлено: *«На первом этапе будет реализован read-only draft mode»*. Пока **только draft**, ни одного write tool'а в публичной API не описано.
- Все запросы фильтруются по `teamId` и роли пользователя (RBAC: Buyer видит только себя, Team Lead — команду). Сервер игнорирует попытки клиента переопределить `cmp_user`/`cmp_team`.
- Лимиты:
  - `query_stats.limit` ≤ 1 000 строк;
  - `export_csv` ≤ 100 000 строк (можно расширить под тариф);
  - длина имени PAT-ключа — 100 символов;
  - срок жизни PAT — настраивается или бессрочный.

### 3.5 Что в MCP видно про Facebook

Через `query_stats` можно сгруппировать по `cmp_source` (источник трафика, добавленный в adset.pro как «Facebook Ads») и по UTM-параметрам (`ext_utm_*`, `ext_sub1`–`ext_sub10`), которые содержат FB Campaign/Adset/Ad ID **если арбитражник их сам зашил в URL**. Метрики при этом — внутренние tracker'a (`clicks`, `cpa_accept`, `revenue`, `roi`, `epc`, `click_to_dep` и т.д.), не из FB Marketing API. Полей `effective_status`, `delivery_status`, `daily_budget` или чего-то близкого к FB Ads Manager — **нет**.

---

## 4. REST API для разработчиков

Это **самое ценное** для FB Stop Bot. Источник: [`/guide_ru/publichnoe-http-api`](https://adset.pro/guide_ru/publichnoe-http-api) (опубликовано 2026-05-20).

### 4.1 Базовые URL

| Что | URL |
|---|---|
| Production base | `https://adset.pro` |
| Swagger UI (актуальный) | `https://adset.pro/api/docs` (HTML, рендерится через SPA) |
| OpenAPI JSON | `https://adset.pro/openapi.json` — **на сегодня 404** (документация ссылается, но реальный путь, видимо, `/v3/public-docs/swagger.json`, тоже 404). Это незакрытый багфикс. |

### 4.2 Аутентификация

- **Personal Access Token (PAT)** — префикс `pat_…`, ~64 символа, заголовок `Authorization: Bearer pat_…`. PAT принимается **только** на `/api/*`, не на `/mcp`.
- **OAuth 2.1 (auth_code + PKCE)** — токены префикса `oat_…`, принимаются и на `/api/*`, и на `/mcp`. Discovery — те же `.well-known` endpoint'ы.
- Scopes для REST: `api:stats`, `api:stats:export`, `api:stats:meta`.

### 4.3 Endpoint'ы статистики

| Метод | Путь | Скоуп | Назначение |
|---|---|---|---|
| `POST` | `/api/stats/query` | `api:stats` | Отчёт (метрики × группировки + фильтры) |
| `POST` | `/api/stats/export/csv` | `api:stats:export` | Тот же отчёт в CSV |
| `GET` | `/api/stats/meta/metrics` | `api:stats:meta` | Каталог метрик |
| `GET` | `/api/stats/meta/groups` | `api:stats:meta` | Каталог группировок |
| `GET` | `/api/stats/meta/filters` | `api:stats:meta` | Каталог фильтров |
| `GET` | `/api/stats/meta/distinct?field=…&q=…` | `api:stats:meta` | Autocomplete значений |
| `GET` | `/api/stats/meta/os-versions` | `api:stats:meta` | Версии ОС |

### 4.4 StatsQueryDto (тело `/api/stats/query`)

```json
{
  "time": { "preset": "last7", "timezone": "UTC" },
  "groups": ["day", "cmp_campaign"],
  "metrics": ["clicks", "cpa_accept", "revenue", "roi"],
  "filters": [
    { "field": "user_country", "op": "in", "value": ["US","CA"] },
    { "field": "cmp_offer", "op": "eq", "value": "65f0…" }
  ],
  "pagination": { "page": 1, "limit": 100 },
  "sort": { "field": "clicks", "order": "desc" },
  "attributionWindow": { "hours": 24, "eventType": "click" }
}
```

- 8 пресетов времени: `today, yesterday, last7, last30, thisWeek, prevWeek, thisMonth, prevMonth`.
- 11 операторов фильтра: `eq, neq, in, not_in, gt, lt, gte, lte, between, like`.
- Группировки покрывают 5 тематических групп (Time, Cohort/LTV, User/Network, Marketing/UTM/Campaign, Push/Event) — всего **~50 ключей**.
- Метрики разделены на 10 категорий (Traffic & Finance, Landing, PWA, Postlanding, Notify, Conversion, Telegram, Push, Push Attribution, LTV/Cohort) — всего **~70 метрик**.

### 4.5 Webhook'и

В публичной документации **нет** упоминаний about-tracker → external webhooks для реал-тайм событий. Реал-тайм отдача наружу делается через:
- **outgoing postback** (HTTP GET) — конфигурируется как «пиксель» с типом `HTTP Get`, шлёт по событию (`pwa_install`, `hold`, `accept`, `decline`, `trash`);
- **Telegram Bot пиксель** — шлёт в Telegram-канал по тем же событиям.

То есть полноценного webhook subscription model для произвольных подписчиков нет. Если нашему FB Stop Bot нужен поток конверсий в реальном времени — придётся настроить outgoing postback на наш собственный endpoint, аналогично тому, как arbitrage tracker'ы отдают конверсии в FB CAPI.

---

## 5. Можем ли мы как FB Stop Bot встроить adset.pro

### 5.1 Технически — да, очень легко

- DCR работает без модерации (проверено, я зарегистрировал клиента за один curl).
- PAT/OAuth — оба пути полностью документированы.
- REST API можно дёргать прямо из нашего бэкенда (`apps/api/`), используя `httpx.AsyncClient` (мы уже его используем для Vision API). Никакого SDK не нужно — стандартный JSON.
- Если хотим показать пользователю аналитику от Claude через MCP, можем подключить `https://adset.pro/mcp` к Claude Desktop пользователя — никакого нашего участия не требуется, кроме инструкции в README.

### 5.2 Бизнес-сторона / ToS

ToS ([adset.pro/docs/terms-and-conditions](https://adset.pro/docs/terms-and-conditions)) не запрещает third-party integrations. Пункт 4 («Integrations and access keys») явно предусматривает: *«You may connect third-party services by providing tokens, API keys, secrets»*. Никаких ограничений на reverse-направление (сторонняя система ходит в adset.pro API) нет. Пункт 6 («Acceptable Use») запрещает «abuse of infrastructure» — rate limit'ы прописаны (1 000 строк на query, 100 000 на CSV, attributionWindow ≤ 720 часов), они умеренные.

Acceptable Use Policy у них отдельная ([adset.pro/docs/acceptable-use-policy](https://adset.pro/docs/acceptable-use-policy)) — нужно прочитать перед production-использованием.

### 5.3 Контакты

В Privacy/ToS все email'ы скрыты под «\[email protected]» (Cloudflare obfuscation). На странице TribunAff карточка указывает Telegram/Instagram support — но конкретные handle'ы тоже скрыты (placeholder'ы `https://t.me/` и `https://www.instagram.com/`).

**В открытых источниках не нашёл** ни email'а, ни прямого Telegram-handle менеджера. Получить контакт можно только через регистрацию аккаунта на [adset.pro/register](https://adset.pro/register) — после регистрации в UI обычно есть chat-виджет или ссылка на support-канал. **Нужен прямой контакт через регистрацию.**

### 5.4 Ограничения для third-party

| Что | Лимит |
|---|---|
| `query_stats.limit` | ≤ 1 000 строк / запрос |
| `export_csv` | ≤ 100 000 строк / запрос |
| Окно атрибуции push | 1–720 часов |
| Tariff lock | API/MCP доступны на Starter бесплатно, лимит событий — pay-as-you-go ($0.10 / 1k) |
| RBAC | данные ограничены ролью владельца токена — Buyer-токен НЕ увидит чужие команды |
| Скоупы | нельзя расширить за пределы `stats:*` — write/admin функций просто нет в API |

---

## 6. Сравнение с конкурентами

| Tracker | Открытый MCP? | Подключение к FB | Источник |
|---|---|---|---|
| **adset.pro** | **Да, официальный, опубликован 2026-05-19** (`https://adset.pro/mcp`, OAuth 2.1 + PAT) | Только через CAPI + URL-макросы. Нет Marketing API. | [adset.pro/guide_ru/mcp-servis-adset](https://adset.pro/guide_ru/mcp-servis-adset) |
| **Keitaro** | Только **community/third-party** wrapper-ы (godzilladancer/keitaro-mcp на GitHub), официального **нет** | Через CAPI + макросы (стандарт всех self-hosted трекеров) | [playbooks.com/mcp/godzilladancer/keitaro-mcp](https://playbooks.com/mcp/godzilladancer/keitaro-mcp), [mcpmarket.com/es/server/keitaro](https://mcpmarket.com/es/server/keitaro) |
| **Voluum** | Нет в публичном виде (только их собственная REST API). Официального MCP-анонса не нашёл. | CAPI + макросы | [voluum.com/blog/voluum-pricing](https://voluum.com/blog/voluum-pricing) |
| **RedTrack** | Нет публичного MCP (анонсов в 2025–2026 не найдено) | CAPI + макросы | [redtrack.io/blog/best-bemob-alternatives](https://www.redtrack.io/blog/best-bemob-alternatives/) |
| **BeMob** | Нет MCP | CAPI + макросы | [redtrack.io blog](https://www.redtrack.io/blog/best-bemob-alternatives/) |
| **Binom** | Нет MCP (self-hosted, нет облачного API в этом смысле) | CAPI + макросы | [clickflare.com/blog/voluum-alternatives](https://clickflare.com/blog/voluum-alternatives) |
| **Meta (официальный)** | **Да** — `mcp.facebook.com/ads`, требует App Review | Marketing API напрямую | [Meta announcement](https://www.facebook.com/business/news/meta-ads-ai-connectors) |
| **Pipeboard / GoMarble** | Да (open-source proxy над Marketing API) | Marketing API через OAuth пользователя | [github.com/pipeboard-co/meta-ads-mcp](https://github.com/pipeboard-co/meta-ads-mcp) |
| **Improvado** | Да (commercial MCP wrapper) | Marketing API | [improvado.io/mcp/facebook-ads](https://improvado.io/mcp/facebook-ads) |

**Главный вывод сравнения:** adset.pro — **единственный CIS-tracker с официальным MCP-сервером** на сегодня (май 2026). Это конкурентное преимущество и в то же время указывает на то, что они инвестируют в developer-facing tooling, а не только в UI.

Для нашей задачи (получить данные FB ad'а) ни один из обзорных трекеров — alternative path к Marketing API. Все они — passive recipients событий, как и adset.pro.

---

## 7. Реальные отзывы и критика

Открытые отзывы — скудные. Это объясняется тем, что adset.pro появился публично в 2023, и активная промокампания пошла только с конца 2024-го.

- **CPA.RIP обзор** ([cpa.rip/services/adset-pro-treker/](https://cpa.rip/services/adset-pro-treker/)) — позитивный, акцент на удобстве PWA-конструктора и единой экосистеме. Промокод на 50% скидку на тарифные планы ([cpa.rip/promocode/adset/](https://cpa.rip/promocode/adset/)) предполагает аффилиатские отношения CPA.RIP ↔ adset.pro — это маркетинговый, а не независимый отзыв.
- **TribunAff** ([tribunaff.com/services/adset-pro/](https://tribunaff.com/services/adset-pro/)) — рейтинг 5.0, тоже маркетинговая карточка. Отзывов от пользователей в форме обзора **нет**.
- **Partnerkin / AffLift / CPA.RIP форумы** — отдельной ветки с обсуждением я не нашёл (пробивал партнёрку Reddit, vc.ru, cpa.rip, partnerkin.com). adset.pro упоминается в обзорах как одна из опций, но обсуждения «жалоб/багов» в публичном пространстве не видно. Это нейтральный фон, не позитивный и не негативный.
- **«Практический арбитраж» / @leadgenerals** ([youtube.com/watch?v=fJUBu1dfz_s](https://www.youtube.com/watch?v=fJUBu1dfz_s)) — единственный явный энтузиаст MCP. Андрей Ермолаев, CEO of buying в LeadGenerals ([youtube.com/@leadgenerals](https://www.youtube.com/@leadgenerals)), позиционирует MCP как «соединяющее звено между AI и FB через трекер». Это **маркетинговый** видео-обзор, не независимое review.

**Жалобы и баги, которые я могу констатировать сам:**
- `https://adset.pro/openapi.json` возвращает 404 (404 — `Resource "/v3/public-docs/swagger.json" not found`), хотя в их же документации MCP-сервиса прямо ссылка. Документация и реальность не сошлись.
- SPA-фронт долго мейтится (есть 30-секундный fallback с error-page), что говорит о тяжёлом bundle и не самой быстрой инфраструктуре.
- Их FAQ Dev-Mode для ChatGPT ([adset.pro/guide_ru/faq----dev-mode-v-chatgpt-apps-integracii](https://adset.pro/guide_ru/faq----dev-mode-v-chatgpt-apps-integracii)) откровенно перечисляет **нестабильность**: «Dev Mode плохо подходит для огромных отчётов / тяжёлой аналитики / long polling». То есть прод-уровень MCP-сервиса они сами признают experimental.

---

## Что это значит для FB Stop Bot

1. **Adset.pro не помогает нам читать или менять Facebook напрямую.** Их «подключение к FB» — это исключительно CAPI (events наружу) и URL-макросы (events внутрь). **Они не дают альтернативу Marketing API.** Если мы рассчитывали найти у них обходной канал для гемблинг-кабинетов — его нет.

2. **Но MCP/REST adset.pro даёт нам *post-click* картину**, которой у нас сейчас нет. Vision-браузер парсит Ads Manager (impressions, CTR, CPC, spend, leads-по-FB). Adset.pro знает **что произошло после клика** (PWA install, hold, accept/FTD, redep, LTV, push attribution). Если клиент уже использует adset.pro, **подключить его API к нашему observer'у даёт нам реальную ROI/CR метрику** и позволяет применять стоп-правила вида *«если ROI кампании < -30% за последние 24 ч → STOP»*, а не только *«spend > $50 при 0 leads»*.

3. **Технически интеграция почти бесплатна.** Регистрируем OAuth-клиент через DCR (одна команда `curl`), пользователь идёт через consent-экран `https://adset.pro/oauth/authorize`, мы складываем `access_token` в нашу таблицу `ObserverSettings` (как сейчас храним `VISION_X_TOKEN`). Дальше — `httpx.AsyncClient.post("/api/stats/query", json=dto)`. Никаких новых зависимостей, никакого App Review, никакой playwright-эмуляции.

4. **Маппинг кампаний adset.pro ↔ FB** — нужно реализовать так же, как мы сейчас матчим Offer ↔ Campaign Name. Поскольку adset.pro знает FB Campaign/Adset/Ad ID только если арбитражник зашил их в URL (через `ext_sub4/5/6`), мы можем матчить наш `fb_ad_id` (из data-surface парсинга) c `event.ext_sub6` в adset.pro. **Это даст нам полную цепочку** Ads Manager row ↔ Adset.pro events.

5. **Read-only ограничение MCP — для нашей задачи не критично.** Мы пишем в FB через Vision-браузер. Adset.pro нужен только для **enrichment'а** (добавить ROI / LTV / Click→Dep к каждому FB ad'у). Так что write-доступ от adset.pro нам и не нужен — это упрощает интеграцию.

6. **Реал-тайм через outgoing postback.** Если хотим мгновенно реагировать на конверсию (например, как только FTD приходит — пересчитать ROI кампании и при необходимости STOP), нужно настроить в adset.pro outgoing postback с типом `HTTP Get` на наш endpoint `apps/api/.../adset/postback`. Это уже реализовано как механика для отправки в FB CAPI — мы просто становимся ещё одним подписчиком.

7. **Подключение MCP к UI — отдельная маленькая фича.** Можем в нашем фронте на странице DashboardPage добавить кнопку *«Спросить у Claude/ChatGPT про этот ad»* — открывать pre-filled промпт *«покажи статистику по ext_sub6={fb_ad_id} за последние 7 дней»*. Это не bot-side интеграция, а UX-bridge.

8. **Контакт и онбординг.** Прямого email/Telegram нет в публичных источниках — операционно нужно зарегистрироваться на [adset.pro/register](https://adset.pro/register), найти support в UI и попросить **(а)** подтвердить, что наша backend-интеграция в рамках их Acceptable Use; **(б)** при необходимости — расширенный лимит на `export_csv` (Enterprise tariff даёт private chat и VIP-поддержку). Если клиент уже на их платформе, контакт у него уже есть.

---

## Приложение: ключевые источники

- Официальный сайт: [adset.pro](https://adset.pro)
- MCP-сервис документация: [adset.pro/guide_ru/mcp-servis-adset](https://adset.pro/guide_ru/mcp-servis-adset)
- Публичное HTTP API: [adset.pro/guide_ru/publichnoe-http-api](https://adset.pro/guide_ru/publichnoe-http-api)
- Facebook Pixel (CAPI): [adset.pro/guide_ru/facebook-pixel-conversions-api](https://adset.pro/guide_ru/facebook-pixel-conversions-api)
- Источники трафика: [adset.pro/guide_ru/istochniki-trafika-sources](https://adset.pro/guide_ru/istochniki-trafika-sources)
- Интеграция Keitaro: [adset.pro/guide_ru/integraciya-adset-pro-s-trekerom-keitaro](https://adset.pro/guide_ru/integraciya-adset-pro-s-trekerom-keitaro)
- Telegram Bot: [adset.pro/guide_ru/telegram-bot----otbivka-konversiy-v-telegram-kanal](https://adset.pro/guide_ru/telegram-bot----otbivka-konversiy-v-telegram-kanal)
- Тарифы: [adset.pro/guide_ru/tarifnye-plany---obzor](https://adset.pro/guide_ru/tarifnye-plany---obzor)
- ToS: [adset.pro/docs/terms-and-conditions](https://adset.pro/docs/terms-and-conditions)
- Privacy: [adset.pro/docs/privacy-policy](https://adset.pro/docs/privacy-policy)
- OAuth Authorization Server Metadata: [adset.pro/.well-known/oauth-authorization-server](https://adset.pro/.well-known/oauth-authorization-server)
- OAuth Protected Resource Metadata: [adset.pro/.well-known/oauth-protected-resource](https://adset.pro/.well-known/oauth-protected-resource)
- CPA.RIP обзор: [cpa.rip/services/adset-pro-treker/](https://cpa.rip/services/adset-pro-treker/)
- TribunAff карточка: [tribunaff.com/services/adset-pro/](https://tribunaff.com/services/adset-pro/)
- YouTube обзор MCP: [youtube.com/watch?v=fJUBu1dfz_s](https://www.youtube.com/watch?v=fJUBu1dfz_s)
- Канал «Практический арбитраж»: [youtube.com/@leadgenerals](https://www.youtube.com/@leadgenerals)
- Meta MCP (для сравнения): [facebook.com/business/news/meta-ads-ai-connectors](https://www.facebook.com/business/news/meta-ads-ai-connectors)
- Keitaro MCP (community): [playbooks.com/mcp/godzilladancer/keitaro-mcp](https://playbooks.com/mcp/godzilladancer/keitaro-mcp)
