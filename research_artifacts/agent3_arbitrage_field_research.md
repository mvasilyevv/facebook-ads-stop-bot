# Полевое исследование: Meta Marketing API и MCP в арбитражной практике

Дата: 2026-05-25
Автор: Agent 3 (полевое исследование)
Цель: понять, как реально используют (или не используют) Marketing API и MCP-серверы в арбитраже трафика (gambling/dating/finance/leadgen), чтобы скорректировать продуктовую стратегию FB Stop Bot.

> Дисклеймер: значительная часть открытых источников по теме — маркетинговые блоги SaaS-вендоров (AdKit, AdAdvisor, Ryze, Madgicx, Sovran, Pipeboard, Wevion). Они системно занижают риски использования собственных продуктов и завышают риски конкурентов. Где это критично — я отмечаю биас в самой цитате. Прямых постов «обычных media-buyers» в полностью открытом доступе крайне мало: основное обсуждение этой темы идёт в закрытых сообществах (STM Forum, AffLift платный раздел, приватные Telegram-чаты CIS-команд, Discord MCP-проектов). Поэтому раздел Reddit-цитат тоньше, чем хотелось бы — это объективное ограничение публичных источников, а не сокращение усилия.

---

## 1. Используют ли арбитражники Marketing API вообще

**Короткий ответ:** для серых вертикалей (gambling, dating, крипта, нутра) — почти нет. Подавляющее большинство сидит на DOM-скрейпинге через антидетекты (Dolphin{anty}, AdsPower, Vision, Multilogin, GoLogin). Для условно-белых вертикалей (e-com, leadgen, B2B) — да, есть, но почти всегда не через собственное приложение, а через готовые SaaS (Revealbot, Madgicx, Smartly), которые уже прошли App Review.

**Почему API трудно получить именно арбитражникам:**

- Meta требует обязательную Business Verification + App Review для Advanced Access. Кейс из практики ([Medium, Bilal Ahmad — «The Advanced Access Trap That Nearly Killed My Project»](https://medium.com/@bilal.105.ahmed/facebook-marketing-api-the-advanced-access-trap-that-nearly-killed-my-project-7227ea2ee2c2)): «Some permissions require business verification before you can even apply for Advanced Access. This requirement isn't clearly stated upfront, and the verification process is completely separate from the app review». То есть даже белому SaaS требуется до 2-4 недель и юридическое лицо с бумагами.
- Для запроса доступа нужно сделать минимум один успешный API-вызов с каждой запрашиваемой permission в течение 30 дней до подачи ([Meta App Review Submission Guide](https://developers.facebook.com/docs/app-review/resources/sample-submissions/marketing-api/)).
- Чаще всего арбитражники получают отказ с формулировкой «Developer Policy 1.9 — Build a Quality Product» без конкретики ([Meta Developer Community thread](https://developers.facebook.com/community/threads/2060645687441638/)).
- Gambling-аккаунты дополнительно требуют отдельного разрешения на размещение игорных офферов ([Meta — Apply For Permission to Promote Online Games or Gambling](https://www.facebook.com/business/help/4740325989340856)) — а это априори несовместимо с серой моделью «лей с фарм-аккаунтов через антидетект».

**Что используют вместо API:**

- Антидетект-браузеры с UI-скриптингом. Dolphin {Anty} прямо позиционируется как «designed specifically for Facebook Ads management, multi-account operations, and affiliate campaigns» ([Dolphin Anty Review, AffTank](https://afftank.com/blog/dolphin-anty-review)).
- Согласно обзору [Wevion — Facebook Ads Automation Ecosystem Explained (2026)](https://wevion.ai/en/blog/facebook-ads-automation-ecosystem-explained/): «A parallel ecosystem exists due to the gap between what Meta's official tools provide and what certain advertisers need. Everything in the grey-hat ecosystem starts with Facebook accounts — not personal profiles but Business Managers, ad accounts, Pages, and the profiles that own them.»
- Русскоязычная сцена (FB-Killa, CPA.RIP, Partnerkin) сводит сценарий запуска API к обходному: «extensions that help obtain special codes (tokens) needed for automating advertising in Facebook» ([FB-Killa.pro — 9 расширений](https://fb-killa.pro/threads/9rasshirenij.25046/)) — то есть выдёргивают пользовательский токен из браузера, а не получают официальный System User Token.

**Вертикали, где API всё-таки доходит:**
e-com и B2B leadgen — пути через Meta Business Partners. Smartly.io, Revealbot, AdEspresso, Madgicx работают именно потому, что юридически прошли App Review и являются Marketing Partners. Из обзора [Smartly.io (AdLibrary, 2026)](https://adlibrary.com/posts/smartly-io-review-2026): «Used by companies like Samsung, Spotify, Uber, and Ralph Lauren». Нутра/gambling в их клиентских кейсах не упоминаются.

**Вывод раздела:** наш заказчик (gambling/dating-арбитраж) в подавляющем большинстве случаев не сможет легально пройти App Review для собственного приложения. Это значит, что архитектура FB Stop Bot на DOM-парсинге через Vision — не «временная заплатка», а единственный жизнеспособный путь для текущей вертикали клиента. Marketing API можно использовать как дополнительный канал, но только в режиме «бери чужой одобренный канал» — например, через официальный MCP Meta или через сторонние Business Partners (AdKit, AdAdvisor, Pipeboard).

---

## 2. Бан-риски: API vs браузер

**Главный тезис, который повторяется во всех источниках:** сам по себе API не банит. Банят паттерн поведения — частоту вызовов, количество создаваемых сущностей в единицу времени, и отсутствие «человеческой» паузы.

Цитата из [Blend AI — Will using MCP with my Meta Ads account get it suspended?](https://blend-ai.com/mcp/learn/will-mcp-suspend-meta-ads-account): «The most common cause of bans is using an MCP server that hasn't gone through Meta's App Review process. Even if connected through a fully approved MCP server, making a large volume of changes in a very short period can get accounts flagged, as Meta tracks activity patterns, not just connection types».

Цитата из [Sovran — Meta Ads API Guide (2026)](https://sovran.ai/blog/api-facebook-ads): «What got accounts banned in 2026 was a specific setup: autonomous AI agents pointed straight at the Marketing API, with raw tokens and no human in the loop, retrying on every error until Meta's anomaly detection flagged the pattern». (Биас: Sovran продаёт «безопасную обёртку», поэтому акцент на риске autonomous-агентов выгоден им.)

**Конкретные API-лимиты, после которых начинаются проблемы:**

- Marketing API мутации (create/edit campaigns, adsets, ads) — 100 QPS на пару (app × ad account) ([Meta Marketing API Rate Limiting](https://developers.facebook.com/docs/marketing-api/overview/rate-limiting/)).
- Ошибка `error_code = 613, subcode 1487742` — «There have been too many calls from this ad-account».
- Рекомендация ([Fivetran — How Can I Reduce Facebook API Rate Limit Errors](https://fivetran.com/docs/connectors/applications/facebook-ads/troubleshooting/rate-limit)): держать средний rate request'ов в пределах 80% от лимитов (token-bucket / leaky-bucket).
- На уровне аккаунта: «when the system detects that certain ad accounts generate a large amount of abnormal traffic, Meta will temporarily reduce the API Rate Limit quota» — деградация без явного бана, но с длительным восстановлением.

**Истории про «сгоревшие» System User Token-ы:**
Прямых подтверждённых историй в открытых источниках я не нашёл. Все упоминания сводятся к best-practice ([AdStellar — Secure Facebook API Connection](https://www.adstellar.ai/blog/secure-facebook-api-connection)): не запрашивать лишних permissions (только `ads_management`, `ads_read`, `business_management`), включать exponential backoff на rate-limit, привязывать токен к минимально необходимому набору ad-accounts.

**Сравнение «те же команды, что перешли с браузера на API»:**
Прямых развёрнутых кейсов «было/стало» — не нашёл в открытых источниках. Косвенный сигнал из [AdAdvisor — The Dangers of MCP for Meta Ads](https://adadvisor.ai/blog/the-dangers-of-mcp-for-meta-ads): «through 2025, multiple agency operators reported permanent ad-account restrictions after pointing Claude or Codex at Meta's Marketing API through unofficial connectors». То есть переход с осторожного DOM-скрейпинга (где паттерн «человеческий») на наивный LLM-агент с прямым доступом к API увеличивал риск, а не снижал.

**Почему через API всё равно гоняют через прокси, хотя IP «не важен»:**

- Не подтверждается тезис, что IP не важен. Антифрод-логика Meta использует комбинацию IP, Business Manager ID, паттерна вызовов. На фарм-аккаунтах токены создавались с конкретного географического IP (страна биллинга, страна паспорта в KYC) — если затем API-запросы идут с условного датацентра в US, это уже сигнал.
- В русскоязычной сцене ([FB-Killa](https://fb-killa.pro/threads/working-with-facebook-accounts-antics-proxies-pharma-practice.25347/)) прокси гоняется не для самого API, а потому что инфраструктура — это связка «фарм аккаунта + проверка через UI + создание токена через UI + затем API», и в любой UI-фазе IP всё ещё критичен.

**Вывод раздела:** для FB Stop Bot не имеет смысла рисковать переходом на «голый» API. Безопасная архитектура — гибрид: DOM-парсинг через Vision остаётся первичным источником истины (Ads Manager UI), а API/MCP подключается только как дополнительный канал для тех клиентов, у кого есть white-hat кабинеты с подтверждённым доступом. Отключение объявления — однозначно через UI-клик в Vision (текущая реализация), не через API mutation.

---

## 3. Реальные workflow-ы команд

Открытые описания workflow-ов на 50-500 объявлений в основном маркетинговые. Несколько практических кусков:

**Что используют для мониторинга:**
- Связка «трекер (RedTrack/Voluum/Keitaro) + ручная проверка Ads Manager + Telegram-алерты». Trackerы дают real-time события (depo, registration), Ads Manager — стоимость. Эти данные сводятся вручную или в кастомных дашбордах. См. ([AffLIFT review, PropellerAds](https://propellerads.com/blog/adv-afflift-review/)) — там обсуждают «trackers (Voluum, RedTrack, BeMob, CPV Lab Pro, and others), automation (rules-based bidding, blacklists/whitelists, and scaling logic)».
- На CIS-сцене популярен подход «Telegram-бот от партнёрки + ручной мониторинг»: «Partnerkin's monitoring system analyzes metrics dynamics and provides Telegram bots for partners to control statistics, balance changes, and earnings» ([Partnerkin](https://partnerkin.com/)).

**Как реагируют на стоп-сигналы:**
Здесь есть три явных лагеря.

1. **Meta Automated Rules (нативные).** Преимущество: ничего своего не нужно, бесплатно. Недостатки документированы: «Meta's delivery is not uniform throughout the day. Evaluating rules during low-delivery hours (2–6 AM in your target geo) can produce misleading results because the data for "today" is incomplete» и «Automated rules can't pause Reach and Frequency campaigns» ([LeadEnforce — Troubleshoot Meta Automated Rules](https://leadenforce.com/blog/how-to-troubleshoot-meta-automated-rules-that-are-not-working), [Meta Business — Limits to Automated Rules](https://www.facebook.com/business/help/222640851458826)). Также рулы могут запускаться лишь раз в 30 минут (минимальный интервал) — для арбитража это слишком долго: за 30 минут на gambling-вертикали можно слить $200–500 без депо.
2. **Сторонние сервисы (Revealbot/Birch, Madgicx, AnyTrack).** Revealbot/Birch имеет AND/OR-логику, custom метрики, action chains. Цитата ([RedTrack — Birch alternatives](https://www.redtrack.io/blog/best-birch-alternatives/)): «Revealbot's easy-to-use automated rule constructor offers advanced features to write complex automation not possible in native platforms, such as AND/OR operators, custom metrics, custom timeframes, metric comparison, and ranking comparison». Минусы для арбитража: $99/мес от старта + лимит на ad spend в pricing-tiers, не работает с фарм-аккаунтами (требует API-доступа), не учитывает данные из tracker'а партнёрки (только Meta-метрики). [TheOptimizer (8 Automation Rules)](https://theoptimizer.io/blog/8-automation-rules-top-media-buyers-use-to-scale-meta-ads-safely) описывает связку: «Once you have both Meta's cost data and your tracker's revenue data, you can build automation rules that use the combined, accurate statistics» — то есть продвинутые команды свои данные сводят сами.
3. **Свой бот (наш сценарий).** Это и есть FB Stop Bot. Открытых аналогов в gambling-вертикали через DOM не нашёл — большинство решений собирают своё закрытое.

**Инфраструктура:**
- Python с `facebook-business-sdk` или прямой httpx — самый частый стек для тех, у кого есть API ([Facebook Python Business SDK](https://github.com/facebook/facebook-python-business-sdk)).
- Node.js — у части MCP-серверов (brijr/meta-mcp).
- Для DOM-сценария — Playwright/Puppeteer + антидетект (Vision/Dolphin/Multilogin), что совпадает с нашей архитектурой.

---

## 4. Подводные камни Marketing API в боевой работе

Здесь источники довольно конкретны.

**Attribution windows ломались в 2025-2026:**
- С июня 2025 параметры `use_unified_attribution_setting` и `action_report_time` стали игнорироваться. API теперь mimics Ads Manager settings ([PPC.land — Meta restricts attribution windows](https://ppc.land/meta-restricts-attribution-windows-and-data-retention-in-ads-insights-api/)).
- В январе 2026 убрали 7-day view и 28-day view: «Industry data shows that some advertisers had 30-40% of conversions coming from that 8-28 day window that no longer counts» ([DataSlayer — Attribution Window Removed January 2026](https://www.dataslayer.ai/blog/meta-ads-attribution-window-removed-january-2026)). Для gambling, где задержка от клика до депо может быть 3-7 дней, это критично.
- Цитата из [AdManage — Meta Marketing API Common Challenges](https://admanage.ai/blog/meta-marketing-api-challenges-and-fix): «The Marketing API, Ads Manager UI, Events Manager, Conversions API, and the reporting warehouse each compute metrics on different schedules, with different attribution windows, and through different aggregation paths. A single ad account can legitimately show four different numbers for the same campaign on the same day.»

**Pixel mismatches при дублировании adset через API:**
«When you duplicate an ad set via the API, the pixel configuration doesn't always carry over. If the destination ad account uses a different pixel than the source, your duplicated ad set will either fail silently or worse track conversions to the wrong pixel» (тот же источник AdManage).

**Custom Conversions:**
- Жёсткий лимит 100 custom conversions на ad account ([Meta — Custom Conversions reference](https://developers.facebook.com/docs/marketing-api/reference/custom-conversion/)). Для арбитража с десятком офферов × несколькими событиями (registration, FTD, redepo) это упирается в лимит быстро.
- По задержанным депозитам в gambling прямых подтверждений в открытых источниках о специфических багах не нашёл — но базовое ограничение очевидно: атрибуция через 7-day click убрана, оставлено только 1-day view + 7-day click, длинные funnel-ы (регистрация-депо-редеп через 5-14 дней) не атрибутируются корректно.

**Rate limits на скейле:**
- 100 QPS на пару (app × account) — на 500 объявлениях с обновлением каждые 60 секунд это уже близко к границе.
- Для insights отдельный header `x-fb-ads-insights-throttle` с `app_id_util_pct` и `acc_id_util_pct` ([Meta — Limits & Best Practices](https://developers.facebook.com/docs/marketing-api/insights/best-practices/)).

**Timezone и currency:**
- «Meta API data defaults to the ad account's timezone and currency, and if your warehouse operates on UTC and your Shopify store is in USD, a raw API pull will lead to mismatched daily reports» ([Windsor.ai — Facebook Meta Ads API Guide](https://windsor.ai/guide-to-facebook-meta-ads-api/)).
- Активный баг на Meta Community: «In the Marketing Insights API, time_range filters in requests are working but the results are not respecting the date filter» ([Meta Developer Community thread](https://developers.facebook.com/community/threads/592418868011048/)).

---

## 5. Meta Advantage+ / AI-генерация на практике

**Advantage+ Creative:**
Общий консенсус из обзоров — работает как мультипликатор поверх хорошего креатива, но не вытягивает плохой. Цитата ([AdManage — Advantage+ vs Manual Creative](https://admanage.ai/blog/meta-advantage-plus-vs-manual-creative)): «If your original creative is underwhelming or your offer unappealing, AI tweaks add noise without fixing fundamental issues». И: «Manual approaches can outperform automated ones in campaigns requiring nuanced messaging or precise targeting».

Рекомендация практиков: сначала вручную найти выигрышный креатив, потом включить Advantage+ для дожима. Для gambling/dating «нюансная messaging» = это половина успеха, поэтому полностью отдавать креатив на откуп Meta AI большинство арбитражников не готово.

**Advantage+ Shopping для не-ecom:**
- Meta переименовала Advantage+ Shopping → Advantage+ Sales и добавила app installs + leadgen ([Stackmatix Guide](https://www.stackmatix.com/blog/meta-advantage-plus-shopping-campaigns)).
- Для leadgen есть хак: «One strategy involves passing the values of lead events and calling them purchase events, which will train the algorithm to find better-quality leads» ([Playbook Media — Advantage+ Shopping Hack for Lead Generation](https://www.playbookmedia.com/blog/get-more-from-your-meta-money-part-2-an-advantage-shopping-hack-for-lead-generation/)).
- Для арбитража gambling/dating это в основном неприменимо: серые офферы и так под угрозой бана, экспериментировать с маппингом «лид → purchase» рискованно.

**Тренды в креативах:**
В открытых источниках арбитражники продолжают использовать внешние тулы (Midjourney, ElevenLabs, Sora, локальные генераторы), потому что: (1) полный контроль над содержанием, важный для cloaking/AB-тестов, (2) не привязано к конкретному ad-account, переиспользуется на разных фарм-кабинетах, (3) дешевле в массовом производстве (десятки вариаций).

---

## 6. Официальный Meta MCP (запущен 29 апреля 2026)

**Что это:**
29 апреля 2026 Meta зарелизила официальный Meta Ads MCP + CLI в публичной бете ([Meta for Business — Meta Ads AI Connectors](https://www.facebook.com/business/news/meta-ads-ai-connectors)). 29 tools в 5 категориях: Campaign Creation & Management (5), Product Catalog (10), Accounts/Pages/Assets (3), Dataset Quality & Diagnostics (4), Insights & Performance (7) ([Pasquale Pillitteri — Official Meta Ads MCP for Claude](https://pasqualepillitteri.it/en/news/1707/official-meta-ads-mcp-claude-29-tools-2026)).

Главное преимущество: **OAuth-флоу, никакого Developer App, никакого App Review**. То есть юридически low-risk путь к API даже для тех, кому отказывали в App Review.

**Безопасность:**
- Кампании созданные через MCP — всегда PAUSED. Это hard-rule ([AdMove — Meta's MCP and CLI](https://www.admove.ai/blog/metas-mcp-and-cli-for-advertisers)).
- НО: edits к существующим кампаниям идут сразу в live, без draft-режима. Это серьёзная дырка для арбитража: один неудачный prompt может зануллить бюджеты или поднять CPC.

**Реальные отзывы:**
Прямых отзывов арбитражников не нашёл — все материалы пока написаны блогерами-маркетологами (Ryze, Sovran, Pasquale Pillitteri, ClaudeFast, AdMove, Common Thread Co, AdAge). Все они продают свой SaaS и пишут «это великолепно, но используйте нашу обёртку, чтобы было безопаснее».

**Цитата с критикой (важная, [AdAdvisor](https://adadvisor.ai/blog/the-dangers-of-mcp-for-meta-ads)):** «The MCP gives AI full write access to your live ad account: budget changes, targeting edits, campaign creation. More critically, writes have no safety net: every action hits your live account immediately with no undo, no draft mode, and no confirmation screen.»

**Цитата с предупреждением о hallucinations ([AdAdvisor](https://adadvisor.ai/blog/the-dangers-of-mcp-for-meta-ads)):** «AI agents hallucinate, misinterpret prompts, and take actions you didn't ask for. In May 2026, one deleted a company's entire database because it misunderstood the task. … Meta dumps everything into every response, and a few more questions and you're past 50% working memory where AI becomes exponentially dumber.»

И ещё ([Passionfruit — Meta Ads + Claude MCP: What It Actually Does (and Doesn't)](https://www.getpassionfruit.com/blog/meta-ads-claude-mcp-what-it-actually-does)): «Claude's specific numbers should be treated as draft until confirmed in Ads Manager or via follow-up prompts that re-pull the same query. LLM outputs are probabilistic rather than deterministic, which produces hallucinated metrics. … When asked why performance metrics like ROAS changed, Claude will give a confident multi-paragraph answer that might be correct, partly correct, or completely invented».

**На каких аккаунтах активирован:**
В открытых источниках чёткого ответа нет. Декларируется «for advertisers using Meta Business Suite». Гипотеза: на serious-restricted (фарм-аккаунты, sapphire-аккаунты для gambling, gold-аккаунты от агенток) — OAuth-флоу может не работать или требовать дополнительной верификации. Подтверждения этому я не нашёл.

**Используют ли арбитражники реально:**
По косвенным признакам — нет. Все позитивные кейсы — про e-com / SMB / leadgen с «нормальными» White-hat business manager-ами. Для серых вертикалей официальный MCP пока не закрывает основную боль: нужны не «удобные prompts для управления», а массовый мониторинг 100+ ad accounts с фарма.

---

## 7. Сравнение community MCP-серверов

| Сервер | Tools | Особенности | Биас источника | Где обсуждение |
|---|---|---|---|---|
| **pipeboard-co/meta-ads-mcp** | 29 | 850 stars, 499 commits, BUSL 1.1 license, default PAUSED campaigns. Remote MCP на `mcp.pipeboard.co`. Зрелый, есть HTTP-стрим | Pipeboard продаёт SaaS поверх, обзоры из их экосистемы благосклонны | [GitHub issues](https://github.com/pipeboard-co/meta-ads-mcp/issues), [ClaudeFast comparison](https://claudefa.st/blog/tools/mcp-extensions/meta-ads-mcp-comparison) |
| **serkanhaslak/meta-mcp** | 77 | 24 модуля, охватывает campaign lifecycle + audiences + custom conversions + creatives | Сольный maintainer, open-source. Issues почти нет — мало пользователей | [GitHub](https://github.com/serkanhaslak/meta-mcp) |
| **brijr/meta-mcp** | ~20 | Бэр-минимум, MIT-style. Хорошо как стартовый шаблон, для прод-use слабоват | Сольный разработчик, ноль монетизации | [GitHub](https://github.com/brijr/meta-mcp) |
| **gomarble-ai/facebook-ads-mcp-server** | ~15 | Удобный фолбэк-токен через сервер GoMarble (токен на стороне их сервера) — это даёт low-friction setup, но передаёт доступ третьей стороне | GoMarble — SaaS-вендор, который потенциально может видеть метаданные ваших вызовов | [GitHub](https://github.com/gomarble-ai/facebook-ads-mcp-server) |

**Для арбитражного use case (мониторинг + actions + аналитика) с точки зрения нашего проекта:**

- Самый мощный по покрытию — `serkanhaslak/meta-mcp` (77 tools, включая custom audiences/conversions, что критично для leadgen-связок).
- Самый зрелый и активно поддерживаемый — `pipeboard-co/meta-ads-mcp`. Но business-source license означает, что для коммерческого использования нужно либо лицензировать, либо ждать Apache-2.0 трансфера в 2029.
- Для нашего стэка (Python, async) `pipeboard-co` подходит лучше — он на Python. Остальные на TypeScript/Node.
- Безопасность: ни один из community-серверов не реализует draft-first паттерн (как у AdKit). Все имеют write-доступ напрямую. Это значит, если интегрировать MCP в FB Stop Bot, добавлять собственный confirmation-слой обязательно.

---

## 8. Конкурирующие SaaS и AI-обёртки

### Зрелые SaaS

- **Revealbot / Birch** ([bir.ch](https://bir.ch/facebook-ads/automated-rules)): pause/budget-rules с AND/OR-логикой, custom timeframes, Slack-алерты. От $99/мес. Не работает с фарм-аккаунтами, требует API.
- **Madgicx** ([Foreplay — Madgicx Alternatives](https://www.foreplay.co/post/madgicx-alternatives)): AI-оптимизация, real-time triggers, от $44/мес pricing по spend tier. «Madgicx's automation is shallower than Revealbot's rule engine».
- **AdEspresso** (Hootsuite): больше про A/B-тесты и аналитику, ad-rules слабее.
- **Smartly.io** ([AdLibrary review](https://adlibrary.com/posts/smartly-io-review-2026)): enterprise ($61.5K–126K/год), creative production + campaign management. Не для арбитража.
- **TheOptimizer** ([theoptimizer.io](https://theoptimizer.io/blog/8-automation-rules-top-media-buyers-use-to-scale-meta-ads-safely)): рулы с интеграцией tracker-данных (Voluum, RedTrack). Один из немногих, ориентированных на media-buyers с tracker-связкой. Стоит изучить как прямого функционального конкурента FB Stop Bot.

### Российская сцена

- **Dolphin {cloud}** (продолжение Dolphin {anty} в облаке) — auto-rules, scaling, moderation, all-in-one для арбитража.
- **AdSet.pro** ([TribunAff](https://tribunaff.com/services/adset-pro/), [CPA.RIP](https://cpa.rip/services/adset-pro-treker/)) — tracker + PWA constructor + push, не auto-stop. Передаёт события в FB через Conversion API.
- Своих стоп-ботов с DOM-парсингом в открытом коммерческом виде у CIS-команд не нашёл — это закрытое внутреннее знание команд.

### AI-обёртки поверх Meta MCP

- **AdKit** ([adkit.so](https://adkit.so/features/ads-mcp/meta)): главная фишка — draft-first паттерн. «AdKit makes every change a draft by default, with nothing touching the Meta account until the user approves it». Прошли App Review, Meta Business Partner.
- **AdAdvisor** ([adadvisor.ai](https://adadvisor.ai/blog/the-dangers-of-mcp-for-meta-ads)): тоже Meta Business Partner, OAuth-флоу, акцент на безопасности.
- **Pipeboard** (SaaS поверх pipeboard-co MCP): managed-MCP + auth handling.
- **Ryze** ([get-ryze.ai](https://www.get-ryze.ai/blog/meta-ads-official-mcp-cli-launch)): flat $40/мес AI-обёртка над MCP.
- **Sovran** ([sovran.ai](https://sovran.ai/blog/api-facebook-ads)): creative-production + Meta-publishing, ориентирован на e-com.

**Чего им не хватает (gap для FB Stop Bot):**

1. Никто из них не работает с фарм-аккаунтами через антидетект — все требуют легальный API/OAuth.
2. Никто не интегрирует данные tracker'а партнёрки в FSM стоп-правил (только Revealbot и TheOptimizer ближе всех, но через manual rule setup).
3. Никто не умеет FSM с уровнями WARNING/STOP и идемпотентностью на повторных триггерах.
4. Telegram-first интерфейс с inline-кнопкой «Отключить» — редкость; большинство SaaS = web-dashboard.
5. Нет специфичной поддержки gambling-вертикали с её задержанной атрибуцией депозитов.

---

## Что из этого важно для FB Stop Bot — выводы

1. **Текущая архитектура (DOM-парсинг через Vision) — это не legacy, это правильный выбор для gambling/dating-арбитража.** API-путь закрыт юридически (App Review почти не проходит), а через grey-channels опаснее, чем UI-эмуляция «человеческого» поведения. Не нужно мигрировать на чистый API.

2. **MCP-интеграция должна быть опциональным дополнительным каналом, не заменой DOM.** Для тех клиентов, у кого появятся white-hat кабинеты (например, агентские от Meta Business Partners) — добавить второй коннектор через `pipeboard-co/meta-ads-mcp` или официальный Meta MCP. Архитектурно — слой `AdSourceProvider` с двумя реализациями: `VisionDOMProvider` и `MarketingAPIProvider`.

3. **Если делать MCP-обёртку — обязательно draft-first паттерн.** Все community MCP-серверы пишут в live без подтверждения, это несовместимо с автоматическим отключением рекламы. FB Stop Bot уже частично это делает (DisableTask в очереди + manual approve через Telegram inline-кнопку для не-auto случаев) — это надо сохранить и расширить на любые write-actions через MCP.

4. **На rate-limits закладываться плотно: <80% от 100 QPS на пару (app × account).** Если в будущем добавим MCP-канал — встроить token-bucket с deshboard'ом утилизации (header `x-fb-ads-insights-throttle`), exponential backoff, авто-пауза при `error_code = 4` или `613/1487742`.

5. **Атрибуцию через API использовать только как сверочный канал, не как основной источник истины.** UI Ads Manager сейчас единственное место, где видны те же числа, что в API mimics-режиме после июня 2025. Расхождения tracker партнёрки vs Meta — норма, нужно явно показывать в дашборде «Meta cost vs Tracker revenue» как два разных числа.

6. **Custom conversions через API — лимит 100 на аккаунт. Для арбитража с десятком офферов × несколькими событиями = упрётся быстро.** Если бот будет автогенерировать кастомки — добавить counter и алерт на 80%.

7. **Meta Automated Rules — нативные рулы Meta — не подходят арбитражу из-за минимального интервала 30 минут и того, что результаты ненадёжны вне peak-hours.** Это сильный аргумент в маркетинге FB Stop Bot: «наш цикл сканирования 60 секунд, мы реагируем на стоп-сигналы в 30 раз быстрее Meta Automated Rules».

8. **FSM с уровнями WARNING/STOP — уникальная фича.** Ни один из изученных конкурентов (Revealbot, Madgicx, TheOptimizer) не делает двухуровневой эскалации. Это нужно явно проговаривать в позиционировании.

9. **Интеграция с tracker'ом партнёрки (RedTrack/Voluum/Keitaro/AdSet.pro) — следующий гэп.** Все продвинутые команды сводят данные tracker × Meta вручную. Если FB Stop Bot будет сводить это автоматически в RuleContext (например, через webhook от tracker'а), стоп-правила могут использовать реальный ROI, а не только cost-метрики Meta.

10. **Advantage+ Creative и AI-генерация креативов от Meta — пока не пытаться переносить в продукт.** Для серых вертикалей контроль над креативом критичен. Если делать AI-генерацию, идти через внешние тулы (Midjourney/Sora/ElevenLabs) + ручная сборка, а через бот только посылать готовые креативы в кампании (когда/если будет API-канал).

---

## Ограничения этого исследования

- Многие самые ценные обсуждения по теме идут в закрытых сообществах (STM Forum за $99/мес, AffLift premium, приватные Telegram-чаты CIS-команд, Discord-серверы Pipeboard / brijr / serkanhaslak). Открытые поисковики (WebSearch) дают только публичные блоги и GitHub README. Для следующей итерации рекомендуется: (1) попросить заказчика дать доступ к закрытым форумам, (2) парсить GitHub Issues упомянутых MCP-серверов напрямую через API, (3) делать качественные интервью с 3-5 практикующими media-buyers.
- Не удалось найти ни одной публичной истории «System User Token сгорел из-за X» с конкретикой. Скорее всего, такие истории есть только в приватных чатах.
- Не удалось найти ни одного публичного отзыва арбитражника на официальный Meta MCP (запущен меньше месяца назад). Все материалы — от блогеров-маркетологов и SaaS-вендоров.
- Отсутствие прямых Reddit-цитат — следствие того, что r/FacebookAds и подобные обсуждают в основном начинающих рекламодателей в e-com, не арбитраж. Профессиональная арбитражная сцена сидит в закрытых сообществах.
