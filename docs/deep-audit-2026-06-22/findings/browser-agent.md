# Аудит: Node.js gRPC browser-agent — 2026-06-22

Подсистема: `services/browser-agent/src/` + `clients/python_grpc/client.py`. Read-only.
Прошлый аудит (`docs/audit/AUDIT_2026-06-17.md`) закрыл H4 (холодная вкладка → ждём EAA-токен), L10 (ad_account_id в ListCampaigns), L11 (мёртвый python-клиент). Здесь — НОВОЕ, не повторяю закрытое.

Итог: **0 CRIT, 2 HIGH, 4 MID, 3 LOW.** Money-канал (scan/mutation/lock/heal в одно-кабинетном режиме) в целом здоровый. Основные риски — в мульти-кабинетном (этап 8) периметре self-heal и в untested upload-пути.

---

## HIGH

### H1 — Self-heal сети в мульти-кабинете лечит НЕ ту вкладку → авто-стоп упавшего кабинета остаётся мёртвым
- **severity:** HIGH
- **location:** `services/browser-agent/src/session-manager.ts:476-482` (healLevel 0 reload), `services/browser-agent/src/index.ts:383-390` (вызов heal в скане), `session-manager.ts:551-558` (actId-ветка НЕ трогает `session.primaryPage`)
- **problem:** При сетевом сбое скана кабинета B `healSessionNetwork` на уровне 0 делает `session.primaryPage.reload()`. Но в мульти-кабинете `ensureAdsManagerPage({actId})` возвращает per-cabinet страницу и осознанно НЕ присваивает её в `session.primaryPage` (комментарий «session.primaryPage НЕ трогаем»). Значит heal-reload перезагружает legacy/первую вкладку, а не упавшую вкладку кабинета B. Сеть кабинета B не оживает, пока heal не доэскалирует до level 1/2 (CDP-reconnect / рестарт профиля), что требует ещё ≥1 цикла со streak'ом и cooldown 45с.
- **impact:** Деньги: канал детекта (а значит и авто-стоп) конкретного кабинета восстанавливается медленнее, чем задумано — убыточный ад в этом кабинете крутится лишние циклы. Мульти-кабинет — заявленный прод-режим (этап 8). В одно-кабинетном режиме бага нет (primaryPage и есть рабочая вкладка).
- **fix:** В heal принимать/резолвить страницу по актуальному actId (напр. передавать actId в `healSessionNetwork` и брать `findAdsManagerPageByAct(session.browser, actId)` вместо `session.primaryPage`), либо в actId-ветке `ensureAdsManagerPage` запоминать «последнюю активно сканированную вкладку» в session-поле, по которому работает heal. Минимум — level-0 reload должен бить по странице, чья сеть упала.
- **confidence:** high

### H2 — `netFailureStreak`/`healLevel` — на сессию, а не на кабинет → сбой одного кабинета триггерит лечение всей сессии (и наоборот, маскирует)
- **severity:** HIGH
- **location:** `services/browser-agent/src/session-health.ts:44-58` (`recordFetchOutcome`/`shouldHealNow` по `session`), `index.ts:381-391`, `meta-api/service.ts:93-102`
- **problem:** Счётчик сетевых сбоев и уровень лечения живут на `BrowserSession` (одной на профиль). При последовательном обходе кабинетов: успешный скан кабинета A вызывает `recordFetchOutcome(ok=true)` → обнуляет streak и `healLevel`, даже если кабинет B перед этим накопил сбои. Симметрично: 1 сбой в A + 1 сбой в B = streak 2 → лечение, хотя ни один кабинет не упал устойчиво два раза подряд. Порог «2 подряд» теряет смысл при >1 кабинете.
- **impact:** Деньги/устойчивость: либо ложное лечение (рестарт профиля рвёт ВСЕ кабинеты ради одного блипа в разных), либо маскировка (чередование A-ok/B-fail держит streak около нуля и устойчиво мёртвый кабинет B никогда не лечится). Оба сценария бьют по каналу авто-стопа.
- **fix:** Вести `netFailureStreak`/`healLevel`/`lastHealAt` per (session, actId) — Map на сессии по кабинету; `recordFetchOutcome`/`shouldHealNow`/`healSessionNetwork` принимают actId. Heal-решение и сброс — изолированно по кабинету.
- **confidence:** high

---

## MID

### M1 — UploadVideo: гонка двойной инициализации при concurrent `data`-событиях
- **severity:** MID
- **location:** `services/browser-agent/src/meta-api/service.ts:279-323` (`call.on('data', async ...)` + проверка `videoSession === null`)
- **problem:** Хендлер `data` — `async` и `await videoSession.start()` между проверкой `videoSession === null` и присвоением. grpc-js/EventEmitter может доставить второй `data` event пока первый ждёт `start()`: оба войдут в init-ветку (`videoSession` ещё null), второй создаст НОВЫЙ `VideoUploadSession`, перетёрший первый. `respondedOnce`/`isFinishing` частично прикрывают, но upload_session_id/offset рассинхронятся.
- **impact:** Загрузка видео-крео (creator-путь, не money-авто-стоп) может тихо побиться/упасть. Тестов на `uploadVideoHandler` нет (`grep` пуст) — баг не покрыт.
- **fix:** Сериализовать init: завести `initPromise`/флаг `initStarted` и ставить chunk'и в `pendingQueue` до завершения init, либо делать `call.pause()` на время `start()`. Минимум — гард `initStarted=true` ДО первого `await`.
- **confidence:** med

### M2 — `fetchAllAmTabular`: частичный провал курсорной пагинации отдаёт неполные метрики как валидный скан
- **severity:** MID
- **location:** `services/browser-agent/src/am/am-fetch.ts:238-271`
- **problem:** При пагинации, если страница 1 вернула строки, а страница 2 упала `__amError`/Graph-error, функция возвращает `{ rows: [только стр.1], error }`. `runAmScanWithContext` строит `ScannedAdRow` из того, что есть; `amError` лишь добавляет warning `am_tabular_error`. Ады со страниц >1 молча выпадают из скана. Для лимита 5000 пагинация редка (обычно <5000 ад'ов), но при большом кабинете возможна.
- **impact:** Деньги: выпавшие ады не оцениваются правилами → авто-стоп для них откладывается (выпавший ад = «не в скане» = FSM не трогается, безопасная сторона, но детект для него стоит). Не порча данных, а слепое пятно на хвосте пагинации при ошибке.
- **fix:** При `error` в середине пагинации — НЕ отдавать частичный набор как полный скан: либо вернуть пустой результат (как полный фейл → `outcome="empty"`, повтор на след. цикле), либо явно проставить флаг `partial` и не доверять полноте. Сейчас warning есть, но Python его не использует для гейта.
- **confidence:** med

### M3 — `page-lock._tails` Map растёт неограниченно по session_id (нет удаления ключа)
- **severity:** MID
- **location:** `services/browser-agent/src/page-lock.ts:15-29` (`_tails.set`, нет `delete`)
- **problem:** На каждый новый `session.id` (UUID, новый при каждом `StartBrowser`) в `_tails` добавляется запись и никогда не удаляется (`stopBrowser`/`disconnectBrowser` лок не чистят). При долгой работе процесса с многократными рестартами сессии (recovery-петли, частые StartBrowser после потери сессии) Map монотонно растёт. То же касается `_graphContextCache` в `am-fetch.ts` (по `session:act`).
- **impact:** Медленная утечка памяти в долгоживущем процессе (browser-agent под supervisord, рестарт редкий). Не money, но на пути устойчивости 24/7. Каждая запись мелкая (промис/ctx), деградация месяцами.
- **fix:** Чистить `_tails`/`_graphContextCache` при `stopBrowser`/`disconnectBrowser` (экспортировать `_dropPageLock(sessionId)` и `invalidateGraphContext` для всех act сессии). Либо LRU с потолком.
- **confidence:** high

### M4 — Health full_probe держит per-session page-lock до ~18с, блокируя in-flight авто-стоп
- **severity:** MID
- **location:** `services/browser-agent/src/meta-api/service.ts:121-123` + `meta-api/client.ts:118-125` (`waitForFunction` 10с) + `runNetworkProbe` (`PROBE_TIMEOUT_MS` 8с)
- **problem:** `checkMetaApiHealth({fullProbe})` под `withPageLock(session.id)` вызывает `runNetworkProbe → executeGraphCall`, который сначала `page.waitForFunction(/EAA/, 10_000)`, затем fetch с таймаутом 8с. В худшем случае (токена нет в DOM) probe держит лок ~18с. На это время мутация авто-стопа (`pause_ad`) на той же сессии ждёт в очереди лока.
- **impact:** Деньги: при деградации канала (когда probe как раз и нужен) авто-стоп задерживается до ~18с на каждый watchdog-probe (раз в 300с, плюс кэш probe 60с снижает частоту). Узкое окно, но именно в момент инцидента.
- **fix:** В probe-пути давать `executeGraphCall` укороченный `waitForFunction` (probe и так token-only-проверял наличие токена выше по стеку — можно пропустить повторное ожидание) или вынести probe из общего лока с отдельным коротким таймаутом. Приоритезировать мутации над health в очереди лока.
- **confidence:** med

---

## LOW

### L1 — `getPreferredSession` при отсутствии ads-вкладки берёт «самую свежую» сессию вслепую
- **severity:** LOW
- **location:** `services/browser-agent/src/session-manager.ts:513-527`
- **problem:** Если ни одна сессия не на Ads Manager URL, возвращается `sessions[0]` (по `connectedAt` DESC). Для мутаций без `session_id`/`ad_account_id` (legacy-путь) это может оказаться не та сессия/вкладка. `getPage` затем бросит, если нет primaryPage, но если вкладка есть — мутация уйдёт из произвольной вкладки.
- **impact:** В одно-сессионном проде (одна Vision-сессия) безвреден. При нескольких сессиях — теоретическая неопределённость адресации. Низкая вероятность.
- **fix:** Логировать выбор fallback-сессии; при множественных connected-сессиях требовать явный session_id для мутаций.
- **confidence:** med

### L2 — Дублированный код построения URL вкладки кабинета (DRY-нарушение в money-периметре)
- **severity:** LOW
- **location:** `session-manager.ts:47-52` (`adsManagerUrlForAct`) ≡ `am/am-fetch.ts:111-116` (`cabinetCampaignsUrl`)
- **problem:** Две идентичные функции строят `…/manage/campaigns?act=<id>&<columnsQs>`. Расхождение (напр. кто-то поправит формат в одной) приведёт к тому, что self-heal откроет вкладку не того формата/колонок, чем ожидает скан. Комментарий честно отмечает «единый формат», но физически не общий.
- **impact:** Тех-долг; риск рассинхрона на пути изменений.
- **fix:** Вынести в один экспорт (напр. в `am-columns-preset.ts` или общий `urls.ts`), импортировать в обоих местах.
- **confidence:** high

### L3 — `index.ts` (592 строки) и `session-manager.ts` (702) — крупные файлы со смешением ответственностей
- **severity:** LOW
- **location:** `services/browser-agent/src/index.ts` (592), `services/browser-agent/src/session-manager.ts` (702)
- **problem:** `index.ts` совмещает загрузку proto, все хендлеры BrowserSession+Scanner, toProtoRow, bootstrap сервера. `session-manager.ts` — жизненный цикл + поиск вкладок + heal + мульти-кабинет резолв. Правило проекта «никаких файлов >500 строк в новом коде».
- **impact:** Долг на пути изменений (мульти-кабинет логика размазана). Не корректность.
- **fix:** Вынести Scanner-хендлеры (`runScanCycle`, `listCampaigns`, `hardReload`) и `toProtoRow` в `scanner-handlers.ts`; tab-discovery + heal из session-manager в `tab-resolver.ts`/`session-heal.ts`.
- **confidence:** high

---

## Проверено и признано здоровым (не баг)

- **H4-фикс (ожидание EAA-токена перед fetch)** — на месте (`client.ts:118-125`), мутация на свежей вкладке кабинета не падает мгновенно.
- **`withPageLock` сериализация reload↔fetch** — корректно гасит rejection в хвосте очереди, один сбой не рвёт цепочку.
- **`acquireGraphContext` re-sniff при 190** — `runScanCycle` инвалидирует per-act контекст и повторяет (money-канал самооживляется по токену).
- **Пустой/ошибочный скан НЕ сбрасывает FSM** — Python зовёт `process_scan_rows` только при непустых rows.
- **Owner-scoping (`am-owner.ts`)** — зеркало Python, word-boundary regex совпадает; пустой матч → не сужаем (defense in depth).
- **Listener cleanup в creator-service** (`framenavigated` off в cleanup, attachedPage выставляется до attach) и **ad-library** (page.close в finally) — утечек listener'ов нет.
- **deposits ← results** в `am-join` — берётся из одного ключа (LPV omni/non-omni не суммируются → нет двойного счёта); депозиты для правил берутся отдельно из AdSet.pro.
- **Heartbeat resilience** — ioredis с бесконечным reconnect, запись только при `status==='ready'`, сбой Redis не валит сервис.
- **Graceful shutdown** — SIGINT/SIGTERM → stopHeartbeat → tryShutdown; keepAliveTimer держит loop.
- **Circuit-breaker + session/page recovery (python client)** — NOT_FOUND → start_browser, page-unavailable → reconnect, оба идемпотентны (по одному разу за цикл), транспортные сбои фиксируются в breaker'е, page/session-сбои — нет (правильно).
