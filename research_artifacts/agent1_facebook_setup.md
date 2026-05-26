# Agent 1 — Facebook-side setup for Marketing API + Meta MCP integration

Контекст: FB Stop Bot переводится с DOM-парсинга на Marketing API / официальный Meta MCP. Сейчас 1 BM + 1 Ad Account, архитектура — под «1 BM + N Ad Accounts», далее «N BM от разных владельцев». Вертикали — арбитражные (depositы), что критично для App Review.

---

## A. Meta Business Manager — что должно быть настроено

### A.1 Структура BM

Минимально рабочая конфигурация для нашего use case:

- **Business Portfolio (BM)** клиента — контейнер для всех активов. Один BM на клиента; если у клиента физически разные юрлица — отдельные BM (один личный профиль FB может админить до 2 BM, дальше нужны дополнительные профили — [Sendwin guide 2026](https://blog.send.win/facebook-business-manager-multiple-accounts-multi-account-management-guide-2026/)).
- **Ad Accounts** — добавляются внутрь BM (Owned) или подключаются как Partner-аккаунты (Shared by another BM). Лимиты на количество Ad Accounts в одном BM плавающие и зависят от истории траты (стартует с 1, открывается до 25+ по мере spend).
- **Pages** — каждая Page, под которой крутятся объявления, должна быть либо Owned by BM, либо назначена как Partner asset. Без этого `pages_manage_ads` для неё не выдаётся.
- **System User** (см. раздел C) — служебный «пользователь-робот», к которому привязан long-lived токен и которому assign'ятся ad-аккаунты и pages.
- **Pixel(s)** — на каждый трекаемый воронкой сайт; assign'ится на ad-аккаунты, использующие его в оптимизации.

Под multi-BM future-proofing: бот должен хранить `business_id` рядом с `ad_account_id` и токеном, а не привязываться к глобальному «текущему BM». Все вызовы — через `act_{ad_account_id}` или `/{business_id}/...`, никаких неявных контекстов.

### A.2 Pages — нужен ли admin доступ

**Да, для большей части операций — обязателен.**

- Чтение insights (`act_xxx/insights`) — Page не нужна.
- Pause/resume объявления (PATCH `/{ad_id}` со `status=PAUSED`) — Page не нужна.
- **Создание ads / creatives** — Page-ID обязателен в спецификации creative'а (любой ad с feed-плейсментом требует `page_id`, для Instagram-плейсментов — `instagram_actor_id` от подключённого IG-аккаунта). System User должен иметь права на эту Page через BM, иначе API возвращает `(#1487472) Page not authorized` ([Meta docs, generate access token system user](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/)).
- Для post engagement objective и для boost'а существующих постов — admin-доступ к Page обязателен.

Практически — добавляйте Page в BM как Owned, дайте System User'у роль `ADVERTISE` или `MANAGE` (Advertise хватает для запуска ads с этой Page; Manage нужен только если планируется постить от имени Page через API).

### A.3 Pixel / Conversions API

**Нужно — критично для нашей вертикали.** У нас deposit-события приходят с CPA-сети сервер-к-серверу, веб-пиксель часто пуст. Без CAPI Meta не получает signal для оптимизации и алгоритм деградирует. По данным Meta (агрегированные кейсы), CAPI-only/гибрид даёт ~95%+ event coverage против 50–65% pixel-only ([Weld, 2026](https://weld.app/blog/boost-facebook-conversion-tracking-to-95-with-server-side-tracking-a-step-by-step-guide)). Для iGaming/арбитража FTD (First-Time Deposit) — основной кастом-эвент, и его правильно слать именно через CAPI (или, ещё лучше, через CAPI Gateway/Server-Side GTM с дедупликацией по `event_id`) — [Voluum, iGaming tracking FAQ](https://voluum.com/blog/tracking-igaming-conversions-faq/).

Что должно быть настроено:
- Pixel создан и assign'ен на ad-аккаунт.
- Conversions API endpoint поднят на нашей стороне (или используем CAPI Gateway).
- Custom events: `Deposit`, `FTD`, `Registration` — задаются в Events Manager как Custom Conversions. Это даёт колонки в insights, которые бот может использовать в стоп-правилах (CPA по deposit'ам).
- Domain verification сделан для каждого домена, на который идёт трафик (иначе iOS 14.5+ aggregated events не работают).

### A.4 Catalog

**Не нужен** для базового арбитражного use case (мы не крутим Dynamic Product Ads). Подключать только если будут DPA-кампании на товарные каталоги.

### A.5 Business Verification

**Триггеры (актуально на 2026):**
- Подача app на App Review с advanced permissions (включая `ads_management` для чужих аккаунтов).
- Получение Marketing API Standard / Full Access.
- Высокий месячный spend (внутренний триггер Meta, точный порог не публичный).

Документы для LLC — Articles of Incorporation + tax ID + proof of address; критично, чтобы строки **точно совпадали** (Meta делает строгий match по имени) — [Meta Business Help, required documents](https://www.facebook.com/business/help/193400874040813), [AGrowth 2026 guide](https://agrowth.io/blogs/facebook-ads/how-to-verify-your-business-on-meta). Stop-сценарии: «ООО Ромашка» в Articles vs «Romashka LLC» в bank statement — отказ.

---

## B. Meta App в developers.facebook.com

### B.1 Тип App

**Type: Business.** Этот тип даёт доступ к Marketing API, Business Management API, WhatsApp Business, Instagram Graph API. Consumer/Gaming не дают Marketing API. При создании сразу связать app с BM (поле "Business Account" в настройках). [AdManage 2026 setup guide](https://admanage.ai/blog/meta-ads-api).

### B.2 Products

Минимум:
- **Marketing API** — основной продукт.
- **Facebook Login for Business** — если планируем OAuth flow для multi-tenant (см. раздел E). Для одного клиента с System User'ом можно не подключать.
- **Webhooks** — опционально, для подписки на изменения lead_gen, ad_account и т. п.
- **Conversions API** — отдельный «продукт» в dashboard'е, фактически тот же Graph API endpoint, но нужен для документации use case при App Review.

### B.3 Access tiers: Development → Standard → Full

В мае 2026 Meta переименовала фичу: «Ads Management Standard Access» → **«Marketing API Access Tier»**; «Advanced Access» отображается как **«Full Access»** ([Meta developer blog, 2026 update](https://developers.meta.com/blog/updates-to-ads-management-standard-access-feature/)).

| Tier | Что даёт | Квота (BUC) | Требует Review |
|---|---|---|---|
| **Development** | Только Ad Accounts, к которым у вас есть прямая роль; всё работает, но с жёсткими лимитами | 60 points / 300s; `ads_insights` ~600/час + 400*active_ads | Нет |
| **Standard** (бывш. Standard Access) | Те же Ad Accounts, что и в Dev, но с production-квотой; **нельзя обслуживать чужие бизнесы** без OAuth+app review | 9 000 points / 300s; `ads_insights` ~190 000/час + 400*active_ads | Да (упрощённый) |
| **Full** (бывш. Advanced Access) | Чужие Ad Accounts через user OAuth; enterprise-квота | Кастом, согласовывается | Да (полный App Review + Business Verification) |

Источник по квотам: [get-ryze.ai 2026 free tier guide](https://www.get-ryze.ai/blog/meta-marketing-api-free-tier-limitations-and-quotas), [Meta Marketing API rate limiting docs](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/).

Переход Dev → Standard: запрос в App Dashboard, фича `Marketing API Access Tier`, нужен short justification + ссылка на production app. Standard → Full: полноценный App Review для каждого permission'а отдельно.

### B.4 App Review: реальный процесс

**Сроки** (по [Meta App Review FAQ](https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review/AR-FAQs/) и опыту разработчиков, [dancerscode.com guide](https://dancerscode.com/posts/navigating-the-facebook-app-review-process/), [saurabhdhar.com](https://www.saurabhdhar.com/blog/meta-app-approval-guide)):
- Заявленный Meta SLA — 2–7 дней.
- Реалистично — 5–10 рабочих дней на первый submission; в среднем **1–2 итерации отказов** (по 3–5 дней каждая); полный цикл до approval **2–6 недель**.
- При работе с sensitive vertical (gambling/finance/health) — дольше, может уходить в ручной review, до 4–8 недель.

**Что нужно подготовить:**
1. **App в продакшен-режиме**, должен совершить хотя бы один успешный call за последние 30 дней с тем permission'ом, который запрашивается (иначе автоматический reject).
2. **Privacy Policy URL** на отдельной странице — публично доступна, hosted на том же домене, что и main URL.
3. **Terms of Service URL**.
4. **Business Use Case** — текст 200–500 слов, как и зачем app использует **каждый** permission. Один permission = одна заявка.
5. **Screencast (видео-демо)** на YouTube unlisted, MP4, ≤300 МБ:
   - Полный OAuth flow (с экраном выбора permissions) — на английском.
   - Демонстрация в живом UI приложения, где используется каждый запрошенный scope.
   - Без замазанных областей и закадрового «верьте мне».
6. **Test user credentials** (если в app есть login) или test BM с предзаведённым Ad Account.
7. **Data Use Checkup** заполнен в App Dashboard.
8. **Business Verification** пройдена (требуется до подачи на advanced perms, см. раздел A.5).

**Типичные причины reject** ([Meta App Verification rejection guide](https://developers.facebook.com/docs/app-review/support/rejection-guides/app-verification/), Bilal Ahmad на Medium [Advanced Access Trap](https://medium.com/@bilal.105.ahmed/facebook-marketing-api-the-advanced-access-trap-that-nearly-killed-my-project-7227ea2ee2c2)):
- Скринкаст не показывает OAuth screen или показывает менее, чем все запрошенные scope.
- Permission используется на бэке, а не «for user benefit».
- Privacy Policy не упоминает, какие данные собираются через Meta.
- Test user не может пройти full flow.
- Несоответствие категории app и заявленных permissions.

### B.5 Требования к юрлицу для App Review (2026)

- **Зарегистрированное юрлицо** — LLC, Ltd, ООО, GmbH и т. п. Sole proprietorship с DBA в США иногда проходит, но всё чаще нет.
- **Tax ID / EIN** (US), VAT / company number (EU), ИНН/ОГРН (RU — формально принимается, но в 2024–2026 для российских юрлиц у многих процесс заморожен).
- **Совпадение названий** между Articles of Incorporation, business verification documents, BM Business Info, и app's Display Name (или связанной marketing-страницей).
- **Реально работающий website** на собственном домене с продуктом, который оправдывает запрошенные scope'ы. Лендинг «coming soon» = автоматический reject.

---

## C. System User Token vs User Token vs Page Token

### C.1 Когда какой использовать

| Тип | Срок | Use case | Привязка |
|---|---|---|---|
| **User Access Token** (short-lived) | 1–2 ч | Локальная разработка, тесты в Graph Explorer | Пользователь FB |
| **User Access Token** (long-lived) | ~60 дней | OAuth интеграция, пока пользователь активен | Пользователь FB |
| **System User Token** (regular) | Не истекает по умолчанию (опция `set_token_expires_in_60_days` для security-rotation) | **Production backend для своего BM** | Business Manager |
| **System User Token** (granular, через FB Login for Business) | 60 дней с рефрешем | **Multi-tenant backend, обслуживание чужих BM** | Client business portfolio |
| **Page Access Token** | Долгоживущий, привязан к user или system user | Постинг от имени Page, page-level insights | Page |

Источники: [Meta system user tokens docs](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/), [Meta access token guide](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/).

Для FB Stop Bot в текущем «1 BM» режиме — **regular System User Token, не expiring**. При переходе на multi-tenant — **granular business integration system user tokens per client business** (выдаются через Facebook Login for Business flow).

### C.2 Пошаговая генерация System User Token

1. `business.facebook.com` → Settings → Users → **System Users** → Add.
2. Имя (например, `fb-stop-bot-prod`), роль **Admin** (Employee не даёт API write).
3. **Add Assets**: добавить App (Full Control), все нужные Ad Accounts (Manage Ad Account), все Pages (Manage/Advertise), Pixels (Manage Pixel), Catalogs (если используются).
4. На созданном System User → **Generate New Token** → выбрать App → scope'ы (см. C.3) → **Token never expires** (если не нужна 60-day rotation).
5. Скопировать токен **немедленно** — Meta не показывает его второй раз.
6. Сохранить в secrets (KMS / Vault / Postgres-row с pgcrypto) — **никогда** в .env, никогда в git.
7. Проверить: `GET https://graph.facebook.com/v21.0/me?access_token=<TOKEN>` → должен вернуть system user ID; `GET /me/businesses` → список BM; `GET /me/adaccounts` → список ad-аккаунтов.

### C.3 Scope-маппинг для нашего use case 1–6

| Use case | Scope'ы | App Review нужен? |
|---|---|---|
| 1. Insights (`act_xxx/insights`) | `ads_read`, `read_insights` | `ads_read` — да для Standard, но почти автомат для Marketing API. `read_insights` — да для не-own pages. |
| 2. Pause/Resume ads (`PATCH /{ad_id}`) | `ads_management` | Да, App Review обязателен для production за пределами dev tier. |
| 3. Создание кампаний/ad sets/ads | `ads_management`, `pages_manage_ads` (для page-связанных creative) | Да, оба. |
| 4. Upload креативов (image/video) | `ads_management` (для `/act_xxx/adimages`, `/act_xxx/advideos`); `pages_manage_posts` если из page post | `ads_management` — да; `pages_manage_posts` — да. |
| 5. Custom audiences / lookalikes | `ads_management`, `business_management` (для cross-account audience sharing) | Да оба. |
| 6. Advantage+ / AI creative | `ads_management` + сами Advantage+ фичи не требуют отдельного scope; gating идёт на уровне ad-аккаунта (доступность фичи в данной стране/нише) | Через `ads_management`. |

Дополнительно почти всегда нужны:
- `business_management` — список BM, ad accounts через `/me/businesses`, assignments.
- `pages_show_list` — энумерация Page'ей в BM.
- `pages_read_engagement` — чтение page-level метрик при oбuчении на post.
- `instagram_basic` — если линкуется IG-аккаунт для placement'ов.
- `catalog_management` — только если работаем с DPA.

Источник: [Meta permissions reference](https://developers.facebook.com/docs/permissions/), [Meta Marketing API authorization](https://developers.facebook.com/docs/marketing-api/overview/authorization).

**Что НЕ требует App Review:** все эти scope'ы работают в Dev tier для аккаунтов, где у системного юзера/owner'а app есть прямая роль в BM. Review нужен, чтобы:
- (а) Перешагнуть rate limit Dev tier (Standard tier — обязателен App Review на `ads_management` + `ads_read`);
- (б) Работать с чужими BM/аккаунтами (Full Access).

### C.4 Срок жизни, refresh, revoke

- **Regular System User Token** — формально не истекает. По best practice ([Meta refresh docs](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/)) ротировать ≥1 раз в 60–90 дней через `POST /{app_id}/system_user_access_tokens` (Refresh API). Старый токен остаётся валидным до явного revoke через `DELETE /{user_id}/permissions`.
- **Granular tokens** через FB Login for Business — 60 дней, обязателен auto-refresh за ≥7 дней до expiry.
- **При отзыве** (admin удалил System User / отозвал app / revoke endpoint): все активные токены этого SU моментально невалидны, API возвращает `OAuthException 190`. Бот должен это ловить и алёртить в Telegram (у нас уже есть инфраструктура алёртов, добавить отдельный канал «токен умер»).
- **Token rotation без downtime**: Refresh выдаёт new token; deploy new token → дождаться, что все воркеры подхватили (rolling restart или hot-reload из БД) → revoke old token.

---

## D. Ad Account permissions

В UI BM (для людей) три роли: Admin, Advertiser, Analyst. На уровне Business assignment'а — четыре gradаций: **Admin, Standard (Advertiser), Reports-only (Analyst), Employee**. На System User — **Admin** и **Employee**.

| Действие | Минимальная роль |
|---|---|
| GET insights | Reports-only (Analyst) |
| GET campaigns/adsets/ads metadata | Reports-only |
| PATCH ad status (pause/resume) | Standard (Advertiser) |
| Создание ad/adset/campaign | Standard (Advertiser) |
| Создание custom audience | Standard |
| Изменение billing, добавление users | Admin |

Источники: [Meta ad account roles help](https://www.facebook.com/business/help/155909647811305), [AdAmigo permission errors](https://www.adamigo.ai/blog/fix-meta-ad-account-permission-errors).

**System User** в BM может быть либо Admin (полные права во всём BM), либо Employee (нужно явное per-asset assignment). Для production-бота: **System User = Employee + явный assign Manage на каждый Ad Account, на каждую Page**. Это principle of least privilege и упрощает audit.

Для pause/resume в нашем боте достаточно роли «Standard» на ad-аккаунт. Для создания creative'ов и автогенерации Advantage+ — той же «Standard» хватает.

---

## E. Multi-account и multi-BM scaling

### E.1 Один System User → несколько Ad Accounts в одной BM

Тривиально: один SU, в BM ему assign'ятся 2/5/25 ad-аккаунтов. Бот итерируется: `GET /{system_user_id}/assigned_ad_accounts` или `GET /{business_id}/owned_ad_accounts` + `/{business_id}/client_ad_accounts`. Один токен — все аккаунты. Rate limit считается **на ad account**, не на token (см. F), поэтому горизонтально масштабируется.

### E.2 Доступ к ad-аккаунтам из чужих BM

Три варианта:

**Вариант 1 — Partner Access (client BM шарит свой ad account в наш BM)**
Клиент в своём BM добавляет наш BM как Partner и шарит ad account с ролью «Manage» → этот ad account появляется в **client_ad_accounts** нашего BM → наш System User может assign'нуть его себе → токен нашего SU работает на этом аккаунте. **Простейший путь, не требует Full Access**, но клиент должен доверять нашему BM.

**Вариант 2 — User OAuth (`business_management` + `ads_management`) с Full Access**
Клиент логинится в наше приложение через Facebook Login, авторизует scope'ы; мы получаем long-lived user token и работаем под его учёткой. Это **требует Full Access (App Review)** + Business Verification, потому что app обслуживает «других людей». Это путь для true SaaS.

**Вариант 3 — Facebook Login for Business + Granular business integration system user tokens** (рекомендуемый Meta для tech providers)
Клиент проходит OAuth, в результате в **его** BM создаётся служебный System User, привязанный к нашему app; мы получаем **отдельный токен на каждого клиента**, scoped только к его business portfolio. [Meta FB Login for Business docs](https://developers.facebook.com/documentation/facebook-login/facebook-login-for-business). Это лучший вариант для multi-tenant, потому что:
- Изоляция: токен клиента А не видит данные клиента Б.
- Каждый клиент может revoke независимо.
- Не нужен User Token, который зависит от того, что человек не удалится с FB.

**Требования к App Review для multi-BM:** Standard Access нужен **обязательно** для прода (с Dev tier клиент с 200 объявлениями упрётся в rate limit за минуты). Для granular tokens через FB Login for Business — нужен Standard + прохождение App Review на `ads_management`, `ads_read`, `business_management`.

### E.3 Хранение токенов в multi-tenant

Best practice:
- Таблица `client_credentials (client_id, business_id, ad_account_id, encrypted_token, token_type, expires_at, created_at, last_used_at, last_rotated_at)`.
- Шифрование at-rest: AES-256-GCM с ключом из KMS (AWS KMS / GCP KMS / HashiCorp Vault). Если on-prem — `pgcrypto` + ключ в env, передаваемый через secret manager.
- Никогда не логировать токены, маскировать в любых дампах.
- TTL-ротация: cron каждые 30 дней рефрешит, обновляет `last_rotated_at`. Failed rotation → alert.
- Per-tenant API client: каждый воркер берёт client_id → подтягивает токен по запросу, не кэширует дольше одного цикла.
- Audit log: лог всех API-вызовов с client_id + endpoint + status, retention 90+ дней. Это критично для разбора инцидентов и для отчётности клиенту.
- Rate-limit isolation: per-client semaphore / token bucket, чтобы один шумный клиент не выжигал общий thread pool.

---

## F. Rate limits под нашу нагрузку

### F.1 BUC (Business Use Case) Rate Limits — как считаются

Meta перешла с per-app лимита на **per-(app, business use case, ad account)**. Главный BUC для нас — **Ads Management**. Внутри одной пары `(app_id, ad_account_id, ads_management)`:

- **call_count** — точечный счётчик за окно. Decay 300 секунд.
- **total_cputime** и **total_wall_time** — CPU / wall time для вызовов; даже если call_count низкий, тяжёлые insights могут отрубить по CPU.
- **estimated_time_to_regain_access** — когда снимут throttle.

Возвращается в HTTP header **`X-Business-Use-Case-Usage`** на каждом ответе. JSON формата: `{"<ad_account_id>": [{"type": "ads_management", "call_count": 13, "total_cputime": 5, "total_time": 2, "estimated_time_to_regain_access": 0}]}` ([Meta rate limiting docs](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/)).

Стоимость:
- 1 GET = 1 point.
- 1 POST/PUT/DELETE = 3 points (Insights POST на async-job — отдельная история).
- Пагинация: **каждая страница = отдельный request**, счётчик растёт линейно. `limit=500` дешевле, чем 5 страниц `limit=100`.

### F.2 Числа по tier'ам

- **Dev tier**: 60 points / 300 s; для `ads_insights` — `300 + 40 * active_ads` calls/час.
- **Standard tier**: 9 000 points / 300 s; для `ads_insights` — `190 000 + 400 * active_ads` calls/час.
- Источник: [get-ryze.ai 2026](https://www.get-ryze.ai/blog/meta-marketing-api-free-tier-limitations-and-quotas), [adamigo.ai rate limits](https://www.adamigo.ai/blog/meta-api-rate-limits-vs-scalability), [Meta best practices](https://developers.facebook.com/docs/marketing-api/insights/best-practices/).

### F.3 Расчёт под нашу нагрузку

Сценарий: 1 BM, 1 ad account, ~100 активных объявлений, `GET /act_xxx/insights?level=ad&limit=500` каждые 60 секунд.

- Запрос помещается в одну страницу (100 < 500) → **1 request = 1 point**.
- Частота: 60 запросов в час → 60 points/hour из бюджета.
- Dev tier: лимит ≈ `300 + 40*100 = 4 300` calls/час на insights → используем 60. **~1.4% бюджета.**
- Standard tier: `190 000 + 400*100 = 230 000` calls/час → **~0.03%.**

Вывод: даже на Dev tier по этому одному запросу мы НЕ упрёмся. Но как только добавится:
- pause/resume (3 points каждый), 10 раз в час = 30 points;
- pull campaigns/adsets/ads metadata раз в 10 циклов;
- создание creative (3 points + upload видео отдельной квоты на video processing);

…общий счёт легко вырастает в 10x. Плюс при росте до 5 ad-аккаунтов / 500 объявлений Dev tier перестаёт хватать. **Standard tier нужно получать сразу.**

Тонкости:
- Insights в режиме `async=true` идут под другой счётчик (CPU/wall time), но дают возможность тащить большие данные за горизонт >7 дней.
- Хедер `x-fb-ads-insights-throttle` отдельно для insights (значения 0–100, где 100 = throttled).
- При HTTP 429 / code 17 / subcode 2446079 — обязательный exponential backoff (минимум 60 секунд), не агрессивный retry.

---

## G. Конкретные блокеры для арбитражного use case

### G.1 Реальность App Review для gambling/dating/finance

Meta'шная Online Gambling and Games policy: рекламировать gambling **можно только с явного письменного разрешения Meta**, для своей страны, с лицензией, 18+ disclaimer'ами в каждом креативе ([Meta Transparency Center, gambling policy](https://transparency.meta.com/policies/ad-standards/restricted-goods-services/gambling-games/), [Meta business help apply for permission](https://www.facebook.com/business/help/4740325989340856)). 2026-обновление расширило это на iGaming, sports betting, poker, lotteries ([pmaffiliates обзор 2026](https://pmaffiliates.com/how-to-advertise-online-gambling-on-facebook-in-2026/)).

Это **отдельный от App Review** процесс — `Apply for Permission to Promote Gambling`. Без него — баны ad-аккаунтов независимо от API.

**App Review для app, который явно описывается как «инструмент для арбитража gambling-офферов»:**
- В источниках: на reddit/SO нет конкретных confirmations о approval именно для gambling-арбитража (поисковые запросы выдают пусто). 
- В практических guide'ах (get-passionfruit, AdManage) подразумевается, что App Review проходит для **«general ad management tool»**, а вертикаль ads — забота advertiser'а.
- Реальность скорее всего где-то между: если в video-demo, privacy policy, website описывать app как **«ad performance monitoring and automation platform»** (без слов casino/gambling/arbitrage), а в реальном использовании на одобренном ad-аккаунте крутить что угодно — Meta Review это пропускает. Если в use case явно писать «we automate gambling arbitrage» — почти гарантированный reject.

**Известные подводные камни:**
- App reviewer может зайти на website приложения и увидеть casino-кейсы — это reject.
- Privacy policy не должна перечислять «we collect gambling user data».
- Видео-демо должно показывать generic-ad management, не gambling creatives.
- Business Verification по юрлицу с OKВЭД-кодом из gambling-сферы — может застрять.

### G.2 Можно ли получить Standard Access для app, управляющего объявлениями казино

Технически Marketing API Access Tier не имеет vertical-restriction. Standard Access выдаётся на app в целом. Ограничения работают на уровне:
1. Самого ad-аккаунта (нужен gambling permission).
2. Content policy в момент создания creative (отвергается на лету).

То есть Standard Access **получить реально**, если позиционировать app generic; дальше всё ограничение ложится на ad-аккаунт. Но **Full Access (multi-BM, чужие клиенты)** для gambling-tool'а — практически невозможно: reviewer обязательно зайдёт глубже и найдёт вертикаль.

### G.3 Workaround'ы

1. **Dev tier + один client = один app**. Каждый клиент создаёт свой Meta App в своём BM, мы используем их app credentials. Никакой App Review не нужен, потому что app работает только на own assets. Минус: на стороне клиента нужно техн-онбординг + Dev tier rate limits хватает только до ~5–10 ad accounts с умеренной активностью.
2. **Standard Access для одного «нейтрального» app**, который добавляется в каждый клиентский BM как Partner. Описание app — generic «ad ops platform». Работает до момента, пока Meta Review не зайдёт повторно.
3. **Использовать официальный Meta MCP без своего app** (см. H). Закрывает чтение и базовое управление, но не закроет автотриггеры (бот, который сам каждые 60 сек дёргает API), потому что MCP-сессия Claude-зависимая.
4. **Гибрид**: официальный MCP для интерактивных сценариев + Marketing API через клиентский Dev tier для autoscan'а.

---

## H. Официальный Meta MCP (`https://mcp.facebook.com/ads`)

Запущен Meta 29 апреля 2026 как open beta ([Meta Ads AI Connectors announcement](https://www.facebook.com/business/news/meta-ads-ai-connectors), [mcp.directory deep-dive](https://mcp.directory/blog/meta-ads-cli-mcp)).

### H.1 Что обходим

- **Свой developer app не нужен**: connector сам Meta-authenticated, всё под Meta-owned app.
- **OAuth для каждого scope/business** — один клик Facebook Login.
- **Token rotation** — Meta делает на своей стороне.
- **Business Verification под наш юр.** — не требуется (Meta верит своему собственному login).
- **App Review** — не требуется в принципе.
- **Rate limit headers** скрыты от пользователя; внутри ~200 calls/hour per ad account ([adkit overview](https://adkit.so/features/ads-mcp/meta), [AdAmigo MCP limitations](https://www.adamigo.ai/blog/meta-ads-mcp-limitations-beyond-connector)).

### H.2 Что остаётся обязательным

- **Сам пользователь должен иметь роль** в BM/ad account/page (Meta MCP не выдаёт permissions, только использует существующие).
- **Pages, pixels, catalogs** должны быть assigned в BM как обычно.
- **CAPI для deposit-эвентов** — никакого MCP это не заменит; нужно ставить отдельно.
- **Gambling-permission** для ad-аккаунта — отдельный процесс, MCP его не обходит.
- **Auth-сессия в Claude/ChatGPT** — токен живёт в AI-клиенте, не в нашем бэке. Это значит: **бот, работающий 24/7 без human-in-loop, через официальный MCP не сделать**. MCP — это интерактивный tool.

### H.3 Multi-BM через официальный MCP

- Один FB-аккаунт пользователя может выбрать «Opt in for current business only» и переключать BM ([adkit setup](https://adkit.so/resources/meta-ads-mcp-setup)).
- При наличии у пользователя ролей в нескольких BM — MCP видит все, но per-сессия требует выбора.
- **Multi-tenant SaaS на официальном MCP — нет.** Каждый клиент должен сам устанавливать connector, под своим Claude/ChatGPT-аккаунтом, под своим FB. Это не серверный подход.
- 29 tools at launch (5 categories): Campaign Creation, Catalog, Accounts/Pages, Dataset Diagnostics, Insights ([pasqualepillitteri 29 tools breakdown](https://pasqualepillitteri.it/en/news/1707/official-meta-ads-mcp-claude-29-tools-2026)).
- Нет batch / bulk-операций — каждый tool call — отдельный API hit; для нашего сценария «обновлять статус 100 ads каждые 60 сек» не подходит.
- Нет webhook'ов / push-уведомлений — pull-only.

### H.4 Гибридная архитектура (рекомендация)

- **Бэкенд воркеров** (observer, disable, enable) → Marketing API через System User Token + Standard Access. Это сердце автоматики.
- **Telegram-команды, аналитические запросы пользователя** → Marketing API через тот же бэкенд.
- **Ad-hoc ad-ops через AI-чат** для самого клиента → официальный Meta MCP в его Claude, отдельно. Не интегрировать с нашим ботом.

Это даёт: contractual автоматизацию через Marketing API, плюс «AI-помощник» поверх через Meta-owned channel без compliance-боли.

---

## Приложение: rollout-чеклист для текущего клиента (1 BM, 1 Ad Account)

1. Создать LLC/убедиться что юрлицо есть и доки в порядке.
2. Создать/перенести Business Manager на корпоративный email админа.
3. Добавить Ad Account, Page, Pixel в BM как Owned.
4. Domain verification + CAPI endpoint up.
5. Создать Meta App (Business type), привязать к BM.
6. Подключить Products: Marketing API, Conversions API.
7. Создать System User `fb-stop-bot-prod` (Admin role) в BM, assign все assets.
8. Сгенерировать non-expiring System User Token с `ads_management`, `ads_read`, `business_management`, `pages_show_list`, `pages_read_engagement`, `read_insights`, `pages_manage_ads`.
9. Положить токен в зашифрованный storage (Vault / KMS / pgcrypto).
10. На Dev tier — реализовать и протестировать все 6 use case'ов.
11. Параллельно подать Business Verification.
12. Подать на Marketing API Standard tier (короткая форма в App Dashboard).
13. Подать на App Review на `ads_management` + `ads_read` + `business_management` + `pages_manage_ads`. Записать честный скринкаст в режиме «generic ad ops platform».
14. После approval — переключить runtime на Standard tier rate limits.
15. Под multi-BM: на следующем клиенте — вариант 1 (Partner Access его ad-аккаунта в наш BM) до тех пор, пока не понадобится Full Access. Хранить per-client токены отдельно.
