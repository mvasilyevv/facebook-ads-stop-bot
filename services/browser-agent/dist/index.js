"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.writeSessionStatusEvent = writeSessionStatusEvent;
exports.streamSessionStatusWithLookup = streamSessionStatusWithLookup;
const grpc = __importStar(require("@grpc/grpc-js"));
const protoLoader = __importStar(require("@grpc/proto-loader"));
const path = __importStar(require("path"));
const session_manager_js_1 = require("./session-manager.js");
const parser_js_1 = require("./parser.js");
const empty_reason_js_1 = require("./empty-reason.js");
const hard_reload_js_1 = require("./hard-reload.js");
const ads_table_js_1 = require("./ads-table.js");
const humanizer_js_1 = require("./humanizer.js");
const toggle_utils_js_1 = require("./toggle-utils.js");
const ads_columns_js_1 = require("./ads-columns.js");
const modal_dismisser_js_1 = require("./modal-dismisser.js");
const creator_service_js_1 = require("./creator-service.js");
const service_js_1 = require("./meta-api/service.js");
const service_js_2 = require("./ad-library/service.js");
const PORT = process.env.GRPC_PORT ? parseInt(process.env.GRPC_PORT, 10) : 50051;
const sessionManager = new session_manager_js_1.SessionManager();
const SESSION_STATUS_HEARTBEAT_MS = 5_000;
const SCAN_TOP_RESET_SETTLE_MS = 700;
const SCAN_POST_REFRESH_MIN_ROWS_WAIT_MS = 12_000;
const SCAN_POST_REFRESH_EXTRA_WAIT_MS = 8_000;
const SCAN_POST_SCROLL_CHANGE_WAIT_MS = 4_000;
const SCAN_POST_SCROLL_POLL_MS = 250;
function loadProto(name) {
    const protoPath = path.resolve(__dirname, '../../../proto/v1', name);
    const packageDefinition = protoLoader.loadSync(protoPath, {
        keepCase: true,
        longs: String,
        enums: String,
        defaults: true,
        oneofs: true,
    });
    return grpc.loadPackageDefinition(packageDefinition);
}
function grpcCodeForError(err) {
    const message = String(err?.message || '').toLowerCase();
    return message.includes('not found') || message.includes('не найден')
        ? grpc.status.NOT_FOUND
        : grpc.status.INTERNAL;
}
function getPage(session, pageId) {
    if (pageId) {
        // Задача на будущее: поддержать несколько страниц, когда появится стабильная привязка page_id.
        throw new Error('Поддержка нескольких страниц пока не реализована');
    }
    const preferredPage = (0, session_manager_js_1.findPreferredPrimaryPage)(session.browser);
    if (preferredPage && preferredPage !== session.primaryPage) {
        session.primaryPage = preferredPage;
    }
    const primaryPageClosed = typeof session.primaryPage?.isClosed === 'function' && session.primaryPage.isClosed();
    if (!session.primaryPage || primaryPageClosed) {
        throw new Error('Основная страница браузера недоступна');
    }
    return session.primaryPage;
}
function getSessionForOptionalId(sessionId) {
    const normalizedSessionId = String(sessionId || '').trim();
    return normalizedSessionId
        ? sessionManager.getSession(normalizedSessionId)
        : sessionManager.getPreferredSession();
}
async function confirmMetaDialogIfPresent(page, targetState) {
    const confirmWords = targetState
        ? ['подтвердить', 'ok', 'да', 'продолжить', 'включить', 'confirm', 'yes', 'publish', 'опубликовать']
        : ['подтвердить', 'ok', 'да', 'продолжить', 'отключить', 'confirm', 'yes', 'pause', 'приостановить', 'publish', 'опубликовать'];
    try {
        const buttons = await page.$$('[role="dialog"] button, [role="dialog"] [role="button"], '
            + '[role="alertdialog"] button, [role="alertdialog"] [role="button"]');
        for (const button of buttons) {
            const text = String((await button.innerText()) || '').toLowerCase().trim();
            if (!text || !confirmWords.some((word) => text.includes(word))) {
                continue;
            }
            await (0, humanizer_js_1.humanClick)(page, button, { doubleCheckPause: false });
            await sleep(700);
            return true;
        }
    }
    catch {
        // Ошибка чтения диалога не должна обрывать основной клик по toggle.
    }
    return false;
}
async function readToggleStateFromHandle(toggle) {
    const ariaChecked = await toggle.getAttribute('aria-checked');
    if (ariaChecked !== null) {
        return ariaChecked || 'null';
    }
    try {
        const inputChecked = await toggle.evaluate((node) => {
            if (node instanceof HTMLInputElement && node.type === 'checkbox') {
                return node.checked ? 'true' : 'false';
            }
            return null;
        });
        if (inputChecked) {
            return inputChecked;
        }
    }
    catch {
        // Если handle устарел, внешний retry заново найдёт toggle.
    }
    return 'unknown';
}
async function clickToggleAttempt(page, toggle, attempt) {
    if (attempt === 1) {
        await (0, humanizer_js_1.humanClick)(page, toggle);
        return;
    }
    if (attempt === 2) {
        await (0, humanizer_js_1.humanClick)(page, toggle, { doubleCheckPause: false });
        return;
    }
    await toggle.focus().catch(() => undefined);
    await (0, humanizer_js_1.humanPressKey)(page, 'Space');
}
// --- Обработчики BrowserSessionService ---
async function startBrowser(call, callback) {
    try {
        const req = call.request;
        const session = await sessionManager.startBrowser({
            visionXToken: req.vision_x_token,
            visionApiUrl: req.vision_api_url || 'http://127.0.0.1:3030',
            visionProfileId: req.vision_profile_id,
            visionFolderId: req.vision_folder_id || undefined,
            viewportWidth: req.viewport_width || 1280,
            viewportHeight: req.viewport_height || 800,
        });
        callback(null, {
            session_id: session.id,
            profile: {
                folder_id: session.visionFolderId,
                profile_id: session.visionProfileId,
                cdp_port: session.cdpPort,
            },
            initial_page_url: session.primaryPage?.url() || '',
            pages: [],
        });
    }
    catch (err) {
        callback({
            code: grpc.status.INTERNAL,
            message: err.message || 'Не удалось запустить браузер',
        });
    }
}
async function disconnectBrowser(call, callback) {
    try {
        await sessionManager.disconnectBrowser(call.request.session_id);
        callback(null, {});
    }
    catch (err) {
        callback({ code: grpc.status.NOT_FOUND, message: err.message });
    }
}
async function stopBrowser(call, callback) {
    try {
        await sessionManager.stopBrowser(call.request.session_id);
        callback(null, {});
    }
    catch (err) {
        callback({ code: grpc.status.NOT_FOUND, message: err.message });
    }
}
async function reconnectBrowser(call, callback) {
    try {
        const req = call.request;
        const session = await sessionManager.reconnectBrowser(req.session_id, {
            visionXToken: req.vision_x_token || undefined,
            visionApiUrl: req.vision_api_url || undefined,
            visionProfileId: req.vision_profile_id || undefined,
        });
        callback(null, {
            session_id: session.id,
            profile: {
                folder_id: session.visionFolderId,
                profile_id: session.visionProfileId,
                cdp_port: session.cdpPort,
            },
            initial_page_url: '',
            pages: [],
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function getSessionInfo(call, callback) {
    try {
        const session = getSessionForOptionalId(call.request.session_id);
        const page = getPage(session);
        callback(null, {
            session_id: session.id,
            connected: session.status === 'connected',
            current_url: page.url(),
            pages: [],
            connected_since: Math.floor(session.connectedAt.getTime() / 1000),
        });
    }
    catch (err) {
        callback({ code: grpc.status.NOT_FOUND, message: err.message });
    }
}
async function navigate(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        await page.goto(call.request.url, {
            waitUntil: call.request.wait_until || 'domcontentloaded',
        });
        callback(null, { url: page.url() });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
function writeSessionStatusEvent(call, sessionId, lookup) {
    try {
        const session = lookup(sessionId);
        call.write({
            session_id: session.id,
            status: session.status,
            detail: '',
            current_url: session.primaryPage?.url() || '',
            timestamp: Math.floor(Date.now() / 1000),
        });
        return true;
    }
    catch (err) {
        call.write({
            session_id: sessionId,
            status: 'error',
            detail: err.message || 'Не удалось получить статус сессии',
            current_url: '',
            timestamp: Math.floor(Date.now() / 1000),
        });
        return false;
    }
}
function streamSessionStatusWithLookup(call, lookup) {
    const sessionId = String(call.request?.session_id || '');
    let closed = false;
    const timer = setInterval(() => {
        if (!writeSessionStatusEvent(call, sessionId, lookup)) {
            closeStream(true);
        }
    }, SESSION_STATUS_HEARTBEAT_MS);
    function closeStream(endCall) {
        if (closed)
            return;
        closed = true;
        clearInterval(timer);
        if (endCall && typeof call.end === 'function') {
            call.end();
        }
    }
    timer.unref?.();
    if (!writeSessionStatusEvent(call, sessionId, lookup)) {
        closeStream(true);
        return;
    }
    call.on('cancelled', () => closeStream(false));
    call.on('close', () => closeStream(false));
    call.on('error', () => closeStream(false));
}
function streamSessionStatus(call) {
    streamSessionStatusWithLookup(call, (sessionId) => sessionManager.getSession(sessionId));
}
// --- Обработчики ScannerService ---
function sameStringList(left, right) {
    if (left.length !== right.length)
        return false;
    return left.every((value, index) => value === right[index]);
}
async function waitForInitialAdsRows(page, timeoutMs, isCancelled) {
    const { rows } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
        timeoutMs,
        pollMs: 500,
        isCancelled,
    });
    if (rows.length === 0) {
        console.warn(`Browser-agent: строки таблицы не появились за ${Math.round(timeoutMs / 1000)}с после refresh`);
    }
    // Оптимизация: 2 совпадения вместо 3, ранний выход при count ≥ 5 уже на 2-м совпадении.
    await waitForDomStable(page, 3.0, 0.1, isCancelled);
}
async function prepareAdsTableForScan(page, options, isCancelled) {
    const { doRefresh, resetFirst, settleDelayMs } = options;
    // Перед refresh уходим наверх, чтобы Meta обновляла таблицу из начала списка, а не из середины виртуального окна.
    if (resetFirst) {
        if (isCancelled?.())
            return;
        await (0, ads_table_js_1.resetAdsTableScroll)(page);
        // settle после reset убран: waitForInitialAdsRows + waitForDomStable ниже сами дождутся рендера.
    }
    if (doRefresh) {
        if (isCancelled?.())
            return;
        await (0, parser_js_1.refreshTable)(page);
        if (isCancelled?.())
            return;
        // settleDelayMs игнорируем намеренно: waitForInitialAdsRows ждёт реальные строки, а не фиксированный sleep.
        await waitForInitialAdsRows(page, Math.max(SCAN_POST_REFRESH_MIN_ROWS_WAIT_MS, settleDelayMs + SCAN_POST_REFRESH_EXTRA_WAIT_MS), isCancelled);
        if (isCancelled?.())
            return;
        // Второй reset нужен после refresh: Meta во время обновления данных
        // может оставить виртуальное окно таблицы НЕ наверху, и pass 1 основного
        // цикла увидит «середину» списка, а первые объявления выпадут до того
        // как scroll до них дойдёт. resetAdsTableScroll + waitForDomStable
        // гарантируют что мы начинаем парсинг с верхней границы виртуального окна.
        await (0, ads_table_js_1.resetAdsTableScroll)(page);
        if (isCancelled?.())
            return;
        await waitForDomStable(page, 1.5, 0.1, isCancelled);
    }
}
async function waitForVisibleRowsAfterScroll(page, beforeIds, timeoutMs = SCAN_POST_SCROLL_CHANGE_WAIT_MS, isCancelled) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (isCancelled?.())
            return false;
        await sleep(SCAN_POST_SCROLL_POLL_MS);
        const currentIds = await (0, ads_table_js_1.getVisibleAdsTableRowIds)(page);
        if (currentIds.length === 0)
            continue;
        if (beforeIds.length === 0 || !sameStringList(beforeIds, currentIds)) {
            await waitForDomStable(page, 1.5, 0.1, isCancelled);
            return true;
        }
    }
    return false;
}
async function runScanCycle(call) {
    const req = call.request;
    let cancelled = false;
    call.on('cancelled', () => {
        cancelled = true;
    });
    call.on('close', () => {
        cancelled = true;
    });
    const endIfActive = () => {
        if (!call.destroyed && !call.writableEnded) {
            call.end();
        }
    };
    try {
        const session = sessionManager.getSession(req.session_id);
        const page = getPage(session, req.page_id);
        const maxPasses = req.max_scroll_passes || 50;
        const doRefresh = req.do_refresh !== false;
        const resetFirst = req.reset_scroll_first !== false;
        const settleDelay = (req.settle_delay_seconds || 3) * 1000;
        const startTime = Date.now();
        // Тайминги фаз — для UI/диагностики (поля ScanComplete.phase_timings).
        const phaseTimings = {
            refresh_ms: 0,
            first_row_ms: 0,
            scroll_ms: 0,
            parse_ms: 0,
            total_ms: 0,
        };
        const warnings = [];
        let refreshEndedAt = startTime;
        let firstRowAt = 0;
        let scrollAccumMs = 0;
        let parseAccumMs = 0;
        const allRows = [];
        // fb_ad_id строк, у которых хоть в одном проходе нашёлся spinner-loader или
        // ненайденная ячейка. Отдаются в ScanComplete.partial_row_ids — observer пометит
        // OK_PARTIAL и дочитает эти строки в следующем цикле.
        const accumulatedPartialIds = new Set();
        // fb_ad_id → индекс в allRows. Нужен для апгрейда partial-строки до полной,
        // если она ещё раз попала в DOM (overlap при скролле) и в этот раз все ячейки
        // прочитались. Без апгрейда строка остаётся partial навсегда, потому что
        // виртуализация Ads Manager выбрасывает её после следующего скролла.
        const seenRowIds = new Map();
        let stalledPasses = 0;
        let completedPasses = 0;
        // Не привязываемся к текущим 30 объявлениям: конец списка определяем по нескольким проходам без новых ID.
        const stallLimit = 3;
        const allDismissedModals = [];
        const allUnknownModalArtifacts = [];
        // Закрываем модальные окна до обновления таблицы
        const preModalResult = await (0, modal_dismisser_js_1.dismissKnownModals)(page);
        preModalResult.dismissed.forEach((d) => allDismissedModals.push(d.id));
        preModalResult.unknown.forEach((u) => allUnknownModalArtifacts.push(u.screenshotPath));
        const refreshStartedAt = Date.now();
        await prepareAdsTableForScan(page, {
            doRefresh,
            resetFirst,
            settleDelayMs: settleDelay,
        }, () => cancelled);
        refreshEndedAt = Date.now();
        phaseTimings.refresh_ms = refreshEndedAt - refreshStartedAt;
        // Закрываем модальные окна, которые могли появиться после refresh
        const postRefreshModalResult = await (0, modal_dismisser_js_1.dismissKnownModals)(page);
        postRefreshModalResult.dismissed.forEach((d) => allDismissedModals.push(d.id));
        postRefreshModalResult.unknown.forEach((u) => allUnknownModalArtifacts.push(u.screenshotPath));
        if (cancelled) {
            endIfActive();
            return;
        }
        // Скроллим до стабилизации: Ads Manager держит в DOM только видимый фрагмент таблицы.
        for (let pass = 1; pass <= maxPasses; pass++) {
            if (cancelled)
                break;
            completedPasses = pass;
            // Ждем стабилизации DOM перед чтением видимых строк.
            await waitForDomStable(page, 2.0, 0.1, () => cancelled);
            if (cancelled)
                break;
            // Adaptive wait: возвращается сразу, если ≤10% строк в spinner-загрузке.
            // Иначе ждёт до 10с пока Facebook дозаполнит метрики — иначе snapshot будет
            // полупустой и правила не сработают.
            const parseStart = Date.now();
            const { rows, partialRowIds: passPartialIds } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
                timeoutMs: 10_000,
                pollMs: 500,
                maxPartialRatio: 0.1,
                isCancelled: () => cancelled,
            });
            parseAccumMs += Date.now() - parseStart;
            if (firstRowAt === 0 && rows.length > 0) {
                firstRowAt = Date.now();
            }
            if (cancelled)
                break;
            const passPartialSet = new Set(passPartialIds);
            const newRows = [];
            for (const row of rows) {
                const adId = row.fb_ad_id;
                const existingIndex = seenRowIds.get(adId);
                const nowPartial = passPartialSet.has(adId);
                if (existingIndex === undefined) {
                    // Новая строка — добавляем в allRows и (если partial) в accumulated.
                    const protoRow = toProtoRow(row);
                    seenRowIds.set(adId, allRows.length);
                    allRows.push(protoRow);
                    newRows.push(protoRow);
                    if (nowPartial)
                        accumulatedPartialIds.add(adId);
                }
                else if (accumulatedPartialIds.has(adId) && !nowPartial) {
                    // Апгрейд: ранее партиал, сейчас все ячейки прочитались — обновляем slot.
                    allRows[existingIndex] = toProtoRow(row);
                    accumulatedPartialIds.delete(adId);
                }
                // Иначе: строка уже full ИЛИ всё ещё partial, оставляем как есть.
            }
            const metrics = await (0, ads_table_js_1.getAdsTableScrollMetrics)(page);
            if (cancelled)
                break;
            call.write({
                session_id: req.session_id,
                progress: {
                    pass_number: pass,
                    rows_so_far: allRows.length,
                    scroll_metrics: {
                        found: metrics.found,
                        scroll_top: metrics.scrollTop,
                        max_scroll_top: metrics.maxScrollTop,
                        at_bottom: metrics.atBottom,
                    },
                    new_rows: newRows,
                },
            });
            if (pass >= maxPasses)
                break;
            if (cancelled)
                break;
            const beforeScrollIds = await (0, ads_table_js_1.getVisibleAdsTableRowIds)(page);
            const scrollStart = Date.now();
            const scrollAfter = await (0, ads_table_js_1.scrollAdsTableDown)(page, undefined, () => cancelled);
            if (cancelled)
                break;
            let rowsChangedAfterScroll = false;
            if (!scrollAfter.atBottom) {
                if (scrollAfter.moved) {
                    await waitForDomStable(page, 1.0, 0.1, () => cancelled);
                }
                else {
                    rowsChangedAfterScroll = await waitForVisibleRowsAfterScroll(page, beforeScrollIds, undefined, () => cancelled);
                }
            }
            scrollAccumMs += Date.now() - scrollStart;
            if (cancelled)
                break;
            if (newRows.length > 0 || scrollAfter.moved || rowsChangedAfterScroll) {
                stalledPasses = 0;
            }
            else {
                stalledPasses += 1;
            }
            if (stalledPasses >= stallLimit)
                break;
        }
        // Cold-start защита: если в footer таблицы написано N объявлений, а мы
        // насобирали меньше — значит refresh ещё не дозагрузил таблицу до конца,
        // мы стартовали с виртуальным окном из части строк и dailyскролл просто
        // не нашёл остальных. Ждём и идём второй проход скролла-парсинга, добирая
        // недостающие строки.
        if (!cancelled) {
            const tableTotal = await (0, parser_js_1.getAdsTableTotalCount)(page);
            if (tableTotal !== null && allRows.length < tableTotal) {
                console.log(`[scan] cold-start: собрано ${allRows.length}/${tableTotal}, добираю недостающие`);
                const coldStartDeadline = Date.now() + 30_000;
                let coldStartPasses = 0;
                const coldStartMaxPasses = Math.min(maxPasses, 25);
                while (!cancelled && allRows.length < tableTotal && Date.now() < coldStartDeadline && coldStartPasses < coldStartMaxPasses) {
                    coldStartPasses += 1;
                    // Сбрасываем скролл наверх, чтобы пройти таблицу заново — недостающие
                    // строки могут быть где угодно по диапазону, не только внизу.
                    await (0, ads_table_js_1.resetAdsTableScroll)(page);
                    await waitForDomStable(page, 1.5, 0.1, () => cancelled);
                    if (cancelled)
                        break;
                    let coldStalled = 0;
                    for (let pass = 1; pass <= coldStartMaxPasses; pass++) {
                        if (cancelled)
                            break;
                        if (allRows.length >= tableTotal)
                            break;
                        await waitForDomStable(page, 1.0, 0.1, () => cancelled);
                        if (cancelled)
                            break;
                        const parseStart = Date.now();
                        const { rows: addRows, partialRowIds: addPartial } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
                            timeoutMs: 8_000,
                            pollMs: 400,
                            maxPartialRatio: 0.1,
                            isCancelled: () => cancelled,
                        });
                        parseAccumMs += Date.now() - parseStart;
                        if (cancelled)
                            break;
                        const addPartialSet = new Set(addPartial);
                        let added = 0;
                        let upgraded = 0;
                        for (const row of addRows) {
                            const adId = row.fb_ad_id;
                            const existingIndex = seenRowIds.get(adId);
                            const nowPartial = addPartialSet.has(adId);
                            if (existingIndex === undefined) {
                                seenRowIds.set(adId, allRows.length);
                                allRows.push(toProtoRow(row));
                                if (nowPartial)
                                    accumulatedPartialIds.add(adId);
                                added += 1;
                            }
                            else if (accumulatedPartialIds.has(adId) && !nowPartial) {
                                allRows[existingIndex] = toProtoRow(row);
                                accumulatedPartialIds.delete(adId);
                                upgraded += 1;
                            }
                        }
                        if (added > 0 || upgraded > 0) {
                            coldStalled = 0;
                            console.log(`[scan] cold-start pass=${pass} added=${added} upgraded=${upgraded} total=${allRows.length}/${tableTotal}`);
                        }
                        else {
                            coldStalled += 1;
                        }
                        if (allRows.length >= tableTotal)
                            break;
                        if (coldStalled >= stallLimit)
                            break;
                        const beforeIds = await (0, ads_table_js_1.getVisibleAdsTableRowIds)(page);
                        const scrollStart = Date.now();
                        const scrollAfter = await (0, ads_table_js_1.scrollAdsTableDown)(page, undefined, () => cancelled);
                        if (cancelled)
                            break;
                        if (!scrollAfter.atBottom) {
                            if (scrollAfter.moved) {
                                await waitForDomStable(page, 1.0, 0.1, () => cancelled);
                            }
                            else {
                                await waitForVisibleRowsAfterScroll(page, beforeIds, undefined, () => cancelled);
                            }
                        }
                        scrollAccumMs += Date.now() - scrollStart;
                        if (scrollAfter.atBottom)
                            break;
                    }
                    if (allRows.length < tableTotal) {
                        // Не докрутили — даём Facebook ещё немного и повторяем reset+проход.
                        await sleep(2_000);
                    }
                }
                if (allRows.length < tableTotal) {
                    warnings.push('cold_start_incomplete');
                    console.warn(`[scan] cold-start не дособрал: ${allRows.length}/${tableTotal}`);
                }
                else {
                    console.log(`[scan] cold-start завершён: ${allRows.length}/${tableTotal}`);
                }
            }
        }
        // Re-fetch фаза: если после прохода вниз остались partial-строки, они уже
        // ушли из виртуализированного DOM. Прокручиваем таблицу к началу и идём
        // ещё один полный проход — но НЕ добавляем новые строки, а ТОЛЬКО апгрейдим
        // partial-строки до full когда находим их в DOM с уже подгруженными метриками.
        if (!cancelled && accumulatedPartialIds.size > 0) {
            const partialBefore = accumulatedPartialIds.size;
            console.log(`[scan] re-fetch: остались partial=${partialBefore}, прокручиваю к началу`);
            try {
                await (0, ads_table_js_1.resetAdsTableScroll)(page);
                await waitForDomStable(page, 2.0, 0.1, () => cancelled);
                const refetchMaxPasses = Math.min(maxPasses, 30);
                let refetchStalledPasses = 0;
                for (let pass = 1; pass <= refetchMaxPasses; pass++) {
                    if (cancelled)
                        break;
                    if (accumulatedPartialIds.size === 0)
                        break;
                    await waitForDomStable(page, 1.0, 0.1, () => cancelled);
                    if (cancelled)
                        break;
                    const refetchStart = Date.now();
                    // Дольше ждём, чтобы дать Facebook догрузить именно эти partial-ячейки.
                    const { rows: refetchRows, partialRowIds: refetchPassPartial } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
                        timeoutMs: 8_000,
                        pollMs: 400,
                        maxPartialRatio: 0.0,
                        isCancelled: () => cancelled,
                    });
                    parseAccumMs += Date.now() - refetchStart;
                    if (cancelled)
                        break;
                    const refetchPartialSet = new Set(refetchPassPartial);
                    let upgraded = 0;
                    for (const row of refetchRows) {
                        const idx = seenRowIds.get(row.fb_ad_id);
                        if (idx === undefined)
                            continue;
                        if (accumulatedPartialIds.has(row.fb_ad_id) && !refetchPartialSet.has(row.fb_ad_id)) {
                            allRows[idx] = toProtoRow(row);
                            accumulatedPartialIds.delete(row.fb_ad_id);
                            upgraded += 1;
                        }
                    }
                    if (upgraded > 0) {
                        refetchStalledPasses = 0;
                        console.log(`[scan] re-fetch pass=${pass} upgraded=${upgraded} remaining=${accumulatedPartialIds.size}`);
                    }
                    else {
                        refetchStalledPasses += 1;
                    }
                    if (refetchStalledPasses >= stallLimit)
                        break;
                    if (accumulatedPartialIds.size === 0)
                        break;
                    const beforeIds = await (0, ads_table_js_1.getVisibleAdsTableRowIds)(page);
                    const scrollStart = Date.now();
                    const scrollAfter = await (0, ads_table_js_1.scrollAdsTableDown)(page, undefined, () => cancelled);
                    if (cancelled)
                        break;
                    if (!scrollAfter.atBottom) {
                        if (scrollAfter.moved) {
                            await waitForDomStable(page, 1.0, 0.1, () => cancelled);
                        }
                        else {
                            await waitForVisibleRowsAfterScroll(page, beforeIds, undefined, () => cancelled);
                        }
                    }
                    scrollAccumMs += Date.now() - scrollStart;
                    if (scrollAfter.atBottom)
                        break;
                }
                console.log(`[scan] re-fetch завершён: partial ${partialBefore} → ${accumulatedPartialIds.size}`);
            }
            catch (err) {
                console.warn(`[scan] re-fetch упал: ${err?.message || err}`);
            }
        }
        const duration = (Date.now() - startTime) / 1000;
        if (cancelled) {
            endIfActive();
            return;
        }
        // Финализируем тайминги фаз
        phaseTimings.scroll_ms = scrollAccumMs;
        phaseTimings.parse_ms = parseAccumMs;
        phaseTimings.first_row_ms = firstRowAt > 0 ? firstRowAt - refreshEndedAt : 0;
        phaseTimings.total_ms = Date.now() - startTime;
        // Собираем факты о DOM для empty_reason и warnings
        let tableState = { hasTableHeader: true, hasFilterChips: false };
        try {
            tableState = await page.evaluate(() => {
                const header = document.querySelector('[role="columnheader"]');
                const filterIndicators = document.querySelectorAll('[aria-label*="фильтр" i], [aria-label*="filter" i], [data-testid*="filter" i]');
                return {
                    hasTableHeader: !!header,
                    hasFilterChips: filterIndicators.length > 0,
                };
            });
        }
        catch {
            // Если page.evaluate упал — оставляем дефолт (предполагаем, что хедер есть)
        }
        if (!tableState.hasTableHeader) {
            warnings.push('header_missing_columns');
        }
        const rowsWithAllMetricsEmpty = (0, parser_js_1.countEmptyMetricsRows)(allRows);
        // Объединяем partial-id от парсера (загруженные асинхронно метрики/missing-cells)
        // и от пост-фактум анализа строк (findPartialRows проверяет ad_name/campaign_name).
        const partialRowIds = Array.from(new Set([...accumulatedPartialIds, ...(0, parser_js_1.findPartialRows)(allRows)]));
        const emptyReason = (0, empty_reason_js_1.detectEmptyReason)({
            hasTableHeader: tableState.hasTableHeader,
            hasFilterChips: tableState.hasFilterChips,
            rowCount: allRows.length,
        });
        // Отправляем финальный результат сканирования.
        call.write({
            session_id: req.session_id,
            complete: {
                all_rows: allRows,
                total_passes: completedPasses,
                duration_seconds: duration,
                dismissed_modals: allDismissedModals,
                unknown_modal_artifacts: allUnknownModalArtifacts,
                phase_timings: phaseTimings,
                partial_row_ids: partialRowIds,
                warnings,
                empty_reason: emptyReason ?? '',
                rows_with_all_metrics_empty: rowsWithAllMetricsEmpty,
            },
        });
        endIfActive();
    }
    catch (err) {
        if (cancelled) {
            endIfActive();
            return;
        }
        call.write({
            session_id: req?.session_id || '',
            error: {
                message: err.message || 'Ошибка цикла сканирования',
                recoverable: true,
                attempt: 1,
            },
        });
        endIfActive();
    }
}
async function refreshTableHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const refreshed = await (0, parser_js_1.refreshTable)(page);
        callback(null, { refreshed, fallback_reload: false });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function parseVisibleRows(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const { rows } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
            timeoutMs: 3_000,
            pollMs: 250,
        });
        callback(null, { rows: rows.map(toProtoRow) });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function scrollAndParse(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        if (call.request.wait_for_stable) {
            await waitForDomStable(page, call.request.stable_timeout_seconds || 2.0, 0.1);
        }
        const metricsBefore = await (0, ads_table_js_1.getAdsTableScrollMetrics)(page);
        await (0, ads_table_js_1.scrollAdsTableDown)(page, call.request.scroll_amount || undefined);
        const { rows } = await (0, parser_js_1.waitForParsedAdsRows)(page, {
            timeoutMs: 3_000,
            pollMs: 250,
        });
        const metricsAfter = await (0, ads_table_js_1.getAdsTableScrollMetrics)(page);
        callback(null, {
            new_rows: rows.map(toProtoRow),
            scroll_metrics: {
                found: metricsAfter.found,
                scroll_top: metricsAfter.scrollTop,
                max_scroll_top: metricsAfter.maxScrollTop,
                at_bottom: metricsAfter.atBottom,
            },
            at_bottom: metricsAfter.atBottom,
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function waitForDomStableHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const stabilized = await waitForDomStable(page, call.request.timeout_seconds || 2.0, call.request.poll_interval_seconds || 0.1);
        const rowCount = await page.evaluate(() => document.querySelectorAll('._1gda._2djg').length);
        callback(null, { stabilized, final_row_count: rowCount });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function resetScroll(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const containersReset = await (0, ads_table_js_1.resetAdsTableScroll)(page);
        callback(null, { containers_reset: containersReset });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function getScrollMetricsHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const metrics = await (0, ads_table_js_1.getAdsTableScrollMetrics)(page);
        callback(null, {
            metrics: {
                found: metrics.found,
                scroll_top: metrics.scrollTop,
                max_scroll_top: metrics.maxScrollTop,
                at_bottom: metrics.atBottom,
            },
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function getVisibleRowIds(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const rowIds = await (0, ads_table_js_1.getVisibleAdsTableRowIds)(page);
        callback(null, { row_ids: rowIds });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function findToggleCell(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const cell = await (0, ads_table_js_1.findToggleCellWithTableScan)(page, call.request.fb_ad_id, {
            resetToTop: call.request.reset_to_top,
            maxScrollPasses: call.request.max_scroll_passes > 0 ? call.request.max_scroll_passes : undefined,
        });
        if (cell) {
            const box = await cell.boundingBox();
            const toggle = await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(cell);
            const ariaChecked = toggle ? await readToggleStateFromHandle(toggle) : 'no_toggle';
            callback(null, {
                found: true,
                cell_x: box?.x ?? 0,
                cell_y: box?.y ?? 0,
                aria_checked: ariaChecked,
            });
        }
        else {
            callback(null, { found: false, cell_x: 0, cell_y: 0, aria_checked: '' });
        }
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function readToggleState(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const fbAdId = call.request.fb_ad_id;
        const ariaChecked = await (0, ads_table_js_1.readToggleAriaChecked)(page, fbAdId);
        callback(null, {
            found: ariaChecked !== 'not_found',
            aria_checked: ariaChecked,
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function toggleAd(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const fbAdId = call.request.fb_ad_id;
        const cell = await (0, ads_table_js_1.findToggleCellWithTableScan)(page, fbAdId, { resetToTop: false });
        if (!cell) {
            callback(null, { success: false, final_state: 'not_found' });
            return;
        }
        const toggle = await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(cell);
        if (!toggle) {
            callback(null, { success: false, final_state: 'no_toggle' });
            return;
        }
        const targetChecked = call.request.target_state ? 'true' : 'false';
        const initialChecked = await readToggleStateFromHandle(toggle);
        if (initialChecked === targetChecked) {
            callback(null, { success: true, final_state: initialChecked });
            return;
        }
        let finalState = initialChecked;
        let currentToggle = toggle;
        for (let attempt = 1; attempt <= 3; attempt++) {
            if (attempt > 1) {
                const freshCell = await (0, ads_table_js_1.findToggleCellWithTableScan)(page, fbAdId, {
                    resetToTop: false,
                    maxScrollPasses: 4,
                });
                if (!freshCell) {
                    finalState = 'not_found';
                    continue;
                }
                const freshToggle = await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(freshCell);
                if (!freshToggle) {
                    finalState = 'no_toggle';
                    continue;
                }
                currentToggle = freshToggle;
            }
            const beforeClick = await readToggleStateFromHandle(currentToggle);
            if (beforeClick === targetChecked) {
                callback(null, { success: true, final_state: beforeClick });
                return;
            }
            try {
                await clickToggleAttempt(page, currentToggle, attempt);
            }
            catch (clickErr) {
                finalState = `ошибка_клика: ${clickErr.message || clickErr}`;
                continue;
            }
            await sleep(700);
            await confirmMetaDialogIfPresent(page, Boolean(call.request.target_state));
            await sleep(900);
            // Читаем состояние после клика через тот же поиск строки, что и для toggle.
            finalState = await (0, ads_table_js_1.readToggleAriaChecked)(page, fbAdId);
            if (finalState === targetChecked) {
                callback(null, { success: true, final_state: finalState });
                return;
            }
        }
        callback(null, { success: false, final_state: finalState || 'не_подтверждено' });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function humanMoveHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        await (0, humanizer_js_1.humanMove)(page, call.request.target_x, call.request.target_y, {
            profile: call.request.profile ? mapProtoProfile(call.request.profile) : undefined,
        });
        callback(null, {});
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function humanClickHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const profile = call.request.profile ? mapProtoProfile(call.request.profile) : undefined;
        await (0, humanizer_js_1.humanMove)(page, call.request.x, call.request.y, { profile });
        await sleep(rand(80, 250));
        await page.mouse.down();
        await sleep(rand(60, 180));
        await page.mouse.up();
        await sleep(rand(80, 240));
        callback(null, {});
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function humanWheelScrollHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const anchor = call.request.anchor_x !== undefined && call.request.anchor_y !== undefined
            ? [call.request.anchor_x, call.request.anchor_y]
            : undefined;
        const [finalX, finalY] = await (0, humanizer_js_1.humanWheelScroll)(page, call.request.delta_y, {
            anchor,
            profile: call.request.profile ? mapProtoProfile(call.request.profile) : undefined,
        });
        callback(null, { final_x: finalX, final_y: finalY });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function waitForToggleConfirmation(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const fbAdId = call.request.fb_ad_id;
        const expectedChecked = call.request.expected_checked;
        const requiredReads = call.request.required_reads || 2;
        const pollDelays = call.request.poll_delays_seconds || [0, 3, 3, 3, 3, 4, 4, 5, 5];
        const maxScrollPasses = call.request.max_scroll_passes_restore || 10;
        let readsMatched = 0;
        let lastAriaChecked = '';
        for (let i = 0; i < pollDelays.length; i++) {
            await sleep(pollDelays[i] * 1000);
            // Читаем aria-checked с учётом новой DOM-структуры Ads Manager.
            const ariaChecked = await (0, ads_table_js_1.readToggleAriaChecked)(page, fbAdId);
            lastAriaChecked = ariaChecked;
            if (ariaChecked === expectedChecked) {
                readsMatched++;
                if (readsMatched >= requiredReads) {
                    callback(null, {
                        success: true,
                        message: `Переключатель подтверждён: ${expectedChecked}`,
                        final_aria_checked: ariaChecked,
                        reads_matched: readsMatched,
                    });
                    return;
                }
            }
            else if (ariaChecked === 'not_found' || ariaChecked === 'no_toggle') {
                // Пробуем вернуть переключатель в видимую часть таблицы скроллом.
                readsMatched = 0;
                await restoreToggleVisibility(page, fbAdId, maxScrollPasses);
            }
            else {
                readsMatched = 0;
            }
        }
        callback(null, {
            success: false,
            message: `Переключатель не подтверждён после ${pollDelays.length} попыток. Последнее состояние: ${lastAriaChecked}`,
            final_aria_checked: lastAriaChecked,
            reads_matched: readsMatched,
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function restoreToggleVisibility(page, fbAdId, maxPasses) {
    for (let i = 0; i < maxPasses; i++) {
        const el = await (0, ads_table_js_1.findToggleCellWithTableScan)(page, fbAdId, {
            resetToTop: false,
            maxScrollPasses: 1,
        });
        if (el)
            return;
        await (0, ads_table_js_1.scrollAdsTableDown)(page);
        await sleep(300);
    }
}
// --- Вспомогательные функции ---
async function waitForDomStable(page, timeoutSec, pollIntervalSec, isCancelled) {
    const deadline = Date.now() + timeoutSec * 1000;
    let lastCount = -1;
    let stableCount = 0;
    // Оптимизация latency: 2 совпадения вместо 3; если строк уже ≥ 5 — выходим сразу после 2-го совпадения.
    const REQUIRED_STABLE = 2;
    const EARLY_EXIT_COUNT = 5;
    while (Date.now() < deadline) {
        if (isCancelled?.())
            return false;
        const count = await page.evaluate(() => document.querySelectorAll('._1gda._2djg').length);
        if (isCancelled?.())
            return false;
        if (count === lastCount && count > 0) {
            stableCount++;
            if (stableCount >= REQUIRED_STABLE)
                return true;
            if (count >= EARLY_EXIT_COUNT && stableCount >= 1)
                return true;
        }
        else {
            stableCount = 0;
        }
        lastCount = count;
        await sleep(pollIntervalSec * 1000);
    }
    return stableCount >= 1;
}
function toProtoRow(row) {
    return {
        fb_ad_id: row.fb_ad_id,
        campaign_name: row.campaign_name,
        adset_name: row.adset_name,
        ad_name: row.ad_name,
        delivery_status: row.delivery_status,
        spend: row.spend,
        budget: row.budget,
        reach: row.reach,
        impressions: row.impressions,
        clicks: row.clicks,
        cpc: row.cpc ?? '',
        ctr: row.ctr ?? '',
        outbound_clicks: row.outbound_clicks,
        outbound_ctr: row.outbound_ctr ?? '',
        landing_page_views: row.landing_page_views,
        cost_per_landing_page_view: row.cost_per_landing_page_view ?? '',
        cost_per_result: row.cost_per_result ?? '',
        cpm: row.cpm ?? '',
        frequency: row.frequency ?? '',
        leads: row.leads,
        cost_per_lead: row.cost_per_lead ?? '',
        registrations: row.registrations,
        cost_per_registration: row.cost_per_registration ?? '',
        deposits: row.deposits,
        resolved_offer_code: row.resolved_offer_code ?? '',
    };
}
function mapProtoProfile(proto) {
    return {
        speedFactor: proto.speed_factor,
        jitterFactor: proto.jitter_factor,
        pauseFactor: proto.pause_factor,
        overshootChance: proto.overshoot_chance,
        idleChance: proto.idle_chance,
        idleDurationMin: proto.idle_duration_min,
        idleDurationMax: proto.idle_duration_max,
        bezierStepsMin: proto.bezier_steps_min,
        bezierStepsMax: proto.bezier_steps_max,
    };
}
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
function rand(min, max) {
    return Math.random() * (max - min) + min;
}
async function validateColumnsHandler(call, callback) {
    try {
        const session = getSessionForOptionalId(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const result = await (0, ads_table_js_1.validateAdsTableColumns)(page);
        callback(null, {
            valid: result.valid,
            missing_columns: result.missingColumns,
            found_columns: result.foundColumns,
            error_message: result.errorMessage || '',
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
function mapProtoColumnWidth(raw) {
    return {
        key: String(raw.key || ''),
        title: String(raw.title || ''),
        surfaceKey: String(raw.surface_key || raw.surfaceKey || ''),
        textNeedles: Array.isArray(raw.text_needles) ? raw.text_needles.map(String) : [],
        widthPx: Number(raw.width_px || raw.widthPx || 0),
    };
}
function mergeColumnWidthTargets(savedTargets) {
    if (savedTargets.length === 0)
        return [];
    const byKey = new Map();
    for (const target of (0, ads_columns_js_1.buildAdsTableColumnWidthTargets)())
        byKey.set(target.key, target);
    for (const target of savedTargets) {
        const fallback = byKey.get(target.key);
        byKey.set(target.key, {
            ...fallback,
            ...target,
            textNeedles: target.textNeedles?.length ? target.textNeedles : fallback?.textNeedles,
        });
    }
    return Array.from(byKey.values());
}
async function captureColumnWidthsHandler(call, callback) {
    try {
        const session = getSessionForOptionalId(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const result = await (0, ads_table_js_1.captureAdsTableColumnWidths)(page);
        callback(null, {
            captured: result.captured,
            column_widths: result.columnWidths.map((column) => ({
                key: column.key,
                title: column.title,
                surface_key: column.surfaceKey,
                width_px: column.widthPx,
                text_needles: column.textNeedles || [],
            })),
            matched_columns: result.matchedColumns,
            error_message: result.errorMessage || '',
            total_width_px: result.totalWidthPx,
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function applyColumnWidthsHandler(call, callback) {
    try {
        const session = getSessionForOptionalId(call.request.session_id);
        const page = getPage(session, call.request.page_id);
        const columnWidths = Array.isArray(call.request.column_widths)
            ? call.request.column_widths.map(mapProtoColumnWidth).filter((column) => (column.key && column.surfaceKey && Number.isFinite(column.widthPx) && column.widthPx > 0))
            : [];
        const result = await (0, ads_table_js_1.applyAdsTableColumnWidthPreset)(page, mergeColumnWidthTargets(columnWidths));
        callback(null, {
            applied: result.applied,
            matched_columns: result.matchedColumns,
            missing_columns: result.missingColumns,
            error_message: result.errorMessage || '',
            adjusted_cells: result.adjustedCells,
            total_width_px: result.totalWidthPx,
        });
    }
    catch (err) {
        const code = grpcCodeForError(err);
        callback({ code, message: err.message });
    }
}
async function hardReloadPageHandler(call, callback) {
    try {
        const session = sessionManager.getSession(call.request.session_id);
        if (!session) {
            callback({ code: grpc.status.NOT_FOUND, message: 'session not found' });
            return;
        }
        const page = getPage(session, call.request.page_id);
        const bypassCache = call.request.bypass_cache !== false;
        const result = await (0, hard_reload_js_1.hardReloadPage)(page, bypassCache);
        callback(null, {
            success: result.success,
            error_message: result.errorMessage ?? '',
            reload_ms: result.reloadMs,
        });
    }
    catch (err) {
        callback({ code: grpcCodeForError(err), message: String(err?.message ?? err) });
    }
}
// --- Запуск сервера ---
function main() {
    const server = new grpc.Server();
    // Загружаем proto-описания сервисов.
    const browserSessionProto = loadProto('browser_session.proto');
    const scannerProto = loadProto('scanner.proto');
    const creatorProto = loadProto('creator.proto');
    const metaApiProto = loadProto('meta_api.proto');
    const adLibraryProto = loadProto('ad_library.proto');
    const browserSessionService = browserSessionProto.fb_agent.browser_session.v1.BrowserSessionService;
    const scannerService = scannerProto.fb_agent.scanner.v1.ScannerService;
    const creatorService = creatorProto.fb_agent.creator.v1.CreatorService;
    const metaApiService = metaApiProto.fb_agent.meta_api.v1.MetaApiService;
    const adLibraryService = adLibraryProto.fb_agent.ad_library.v1.AdLibraryService;
    server.addService(browserSessionService.service, {
        startBrowser,
        disconnectBrowser,
        stopBrowser,
        reconnectBrowser,
        getSessionInfo,
        navigate,
        streamSessionStatus,
    });
    server.addService(scannerService.service, {
        runScanCycle,
        refreshTable: refreshTableHandler,
        parseVisibleRows,
        scrollAndParse,
        waitForDomStable: waitForDomStableHandler,
        resetScroll,
        getScrollMetrics: getScrollMetricsHandler,
        getVisibleRowIds,
        findToggleCell,
        readToggleState,
        toggleAd,
        humanMove: humanMoveHandler,
        humanClick: humanClickHandler,
        humanWheelScroll: humanWheelScrollHandler,
        waitForToggleConfirmation: waitForToggleConfirmation,
        validateColumns: validateColumnsHandler,
        captureColumnWidths: captureColumnWidthsHandler,
        applyColumnWidths: applyColumnWidthsHandler,
        hardReloadPage: hardReloadPageHandler,
    });
    const creatorHandlers = (0, creator_service_js_1.createCreatorServiceHandlers)(sessionManager);
    server.addService(creatorService.service, {
        runPlan: creatorHandlers.runPlan,
        startRecording: creatorHandlers.startRecording,
        stopRecording: creatorHandlers.stopRecording,
        getRecorderStatus: creatorHandlers.getRecorderStatus,
    });
    const metaApiHandlers = (0, service_js_1.createMetaApiServiceHandlers)(sessionManager);
    server.addService(metaApiService.service, {
        executeGraphCall: metaApiHandlers.executeGraphCall,
        checkMetaApiHealth: metaApiHandlers.checkMetaApiHealth,
    });
    const adLibraryHandlers = (0, service_js_2.createAdLibraryServiceHandlers)(sessionManager);
    server.addService(adLibraryService.service, {
        searchAds: adLibraryHandlers.searchAds,
        searchAdsBatch: adLibraryHandlers.searchAdsBatch,
        checkAdLibraryHealth: adLibraryHandlers.checkAdLibraryHealth,
    });
    server.bindAsync(`0.0.0.0:${PORT}`, grpc.ServerCredentials.createInsecure(), (error, port) => {
        if (error) {
            console.error(`Не удалось запустить gRPC-сервер: ${error.message}`);
            process.exit(1);
        }
        // Явно держим event loop живым: в detached-запуске gRPC server может не удержать процесс сам.
        const keepAliveTimer = setInterval(() => undefined, 60_000);
        const shutdown = () => {
            clearInterval(keepAliveTimer);
            server.tryShutdown(() => process.exit(0));
        };
        process.once('SIGINT', shutdown);
        process.once('SIGTERM', shutdown);
        console.log(`gRPC-сервер Browser Agent слушает порт ${port}`);
        server.start();
    });
}
if (require.main === module) {
    main();
}
//# sourceMappingURL=index.js.map