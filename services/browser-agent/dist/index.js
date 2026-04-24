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
const grpc = __importStar(require("@grpc/grpc-js"));
const protoLoader = __importStar(require("@grpc/proto-loader"));
const path = __importStar(require("path"));
const session_manager_js_1 = require("./session-manager.js");
const parser_js_1 = require("./parser.js");
const ads_table_js_1 = require("./ads-table.js");
const humanizer_js_1 = require("./humanizer.js");
const toggle_utils_js_1 = require("./toggle-utils.js");
const PORT = process.env.GRPC_PORT ? parseInt(process.env.GRPC_PORT, 10) : 50051;
const sessionManager = new session_manager_js_1.SessionManager();
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
    if (!session.primaryPage) {
        throw new Error('Основная страница браузера недоступна');
    }
    return session.primaryPage;
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
        const session = sessionManager.getSession(call.request.session_id);
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
function streamSessionStatus(call) {
    // Простая реализация: отправляем статус по запросу клиента.
    call.on('data', (req) => {
        try {
            const session = sessionManager.getSession(req.session_id);
            call.write({
                session_id: session.id,
                status: session.status,
                detail: '',
                current_url: session.primaryPage?.url() || '',
                timestamp: Math.floor(Date.now() / 1000),
            });
        }
        catch (err) {
            call.write({
                session_id: req.session_id,
                status: 'error',
                detail: err.message,
                current_url: '',
                timestamp: Math.floor(Date.now() / 1000),
            });
        }
    });
    call.on('end', () => {
        call.end();
    });
}
// --- Обработчики ScannerService ---
async function runScanCycle(call) {
    const req = call.request;
    try {
        const session = sessionManager.getSession(req.session_id);
        const page = getPage(session, req.page_id);
        const maxPasses = req.max_scroll_passes || 50;
        const doRefresh = req.do_refresh !== false;
        const resetFirst = req.reset_scroll_first !== false;
        const settleDelay = (req.settle_delay_seconds || 3) * 1000;
        const startTime = Date.now();
        const allRows = [];
        let seenRowIds = new Set();
        let stalledPasses = 0;
        let completedPasses = 0;
        // Не привязываемся к текущим 30 объявлениям: конец списка определяем по нескольким проходам без новых ID.
        const stallLimit = 3;
        // Обновляем таблицу до сброса, чтобы стартовать сканирование с верхних строк свежего DOM.
        if (doRefresh) {
            await (0, parser_js_1.refreshTable)(page);
            await sleep(settleDelay);
        }
        // Сбрасываем также виртуальный список, где scrollTop может не отражать реальное положение.
        if (resetFirst) {
            await (0, ads_table_js_1.resetAdsTableScroll)(page);
            await sleep(300);
        }
        // Скроллим до стабилизации: Ads Manager держит в DOM только видимый фрагмент таблицы.
        for (let pass = 1; pass <= maxPasses; pass++) {
            completedPasses = pass;
            // Ждем стабилизации DOM перед чтением видимых строк.
            await waitForDomStable(page, 2.0, 0.1);
            // Meta может на короткое время очистить виртуальную таблицу после refresh/scroll.
            const rows = await (0, parser_js_1.waitForParsedAdsRows)(page, {
                timeoutMs: 6_000,
                pollMs: 300,
            });
            const newRows = [];
            for (const row of rows) {
                if (!seenRowIds.has(row.fb_ad_id)) {
                    seenRowIds.add(row.fb_ad_id);
                    const protoRow = toProtoRow(row);
                    newRows.push(protoRow);
                    allRows.push(protoRow);
                }
            }
            const metrics = await (0, ads_table_js_1.getAdsTableScrollMetrics)(page);
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
            const scrollAfter = await (0, ads_table_js_1.scrollAdsTableDown)(page);
            if (newRows.length > 0 || scrollAfter.moved) {
                stalledPasses = 0;
            }
            else {
                stalledPasses += 1;
            }
            if (stalledPasses >= stallLimit)
                break;
        }
        const duration = (Date.now() - startTime) / 1000;
        // Отправляем финальный результат сканирования.
        call.write({
            session_id: req.session_id,
            complete: {
                all_rows: allRows,
                total_passes: completedPasses,
                duration_seconds: duration,
            },
        });
        call.end();
    }
    catch (err) {
        call.write({
            session_id: req?.session_id || '',
            error: {
                message: err.message || 'Ошибка цикла сканирования',
                recoverable: true,
                attempt: 1,
            },
        });
        call.end();
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
        const rows = await (0, parser_js_1.waitForParsedAdsRows)(page, {
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
        const rows = await (0, parser_js_1.waitForParsedAdsRows)(page, {
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
            const ariaChecked = (await toggle?.getAttribute('aria-checked')) || 'unknown';
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
        const initialChecked = (await toggle.getAttribute('aria-checked')) || 'unknown';
        if (initialChecked === targetChecked) {
            callback(null, { success: true, final_state: initialChecked });
            return;
        }
        await (0, humanizer_js_1.humanClick)(page, toggle);
        await sleep(800);
        await confirmMetaDialogIfPresent(page, Boolean(call.request.target_state));
        await sleep(800);
        // Читаем состояние после клика через тот же поиск строки, что и для toggle.
        const ariaChecked = await (0, ads_table_js_1.readToggleAriaChecked)(page, fbAdId);
        callback(null, { success: true, final_state: ariaChecked });
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
        // Для клика по координатам работаем напрямую через мышь страницы.
        await page.mouse.move(call.request.x, call.request.y);
        await page.mouse.down();
        await sleep(rand(60, 180));
        await page.mouse.up();
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
async function waitForDomStable(page, timeoutSec, pollIntervalSec) {
    const deadline = Date.now() + timeoutSec * 1000;
    let lastCount = -1;
    let stableCount = 0;
    while (Date.now() < deadline) {
        const count = await page.evaluate(() => document.querySelectorAll('._1gda._2djg').length);
        if (count === lastCount && count > 0) {
            stableCount++;
            if (stableCount >= 3)
                return true;
        }
        else {
            stableCount = 0;
        }
        lastCount = count;
        await sleep(pollIntervalSec * 1000);
    }
    return stableCount >= 2;
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
        const session = sessionManager.getSession(call.request.session_id);
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
// --- Запуск сервера ---
function main() {
    const server = new grpc.Server();
    // Загружаем proto-описания сервисов.
    const browserSessionProto = loadProto('browser_session.proto');
    const scannerProto = loadProto('scanner.proto');
    const browserSessionService = browserSessionProto.fb_agent.browser_session.v1.BrowserSessionService;
    const scannerService = scannerProto.fb_agent.scanner.v1.ScannerService;
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
    });
    server.bindAsync(`0.0.0.0:${PORT}`, grpc.ServerCredentials.createInsecure(), (error, port) => {
        if (error) {
            console.error(`Не удалось запустить gRPC-сервер: ${error.message}`);
            process.exit(1);
        }
        console.log(`gRPC-сервер Browser Agent слушает порт ${port}`);
        server.start();
    });
}
main();
//# sourceMappingURL=index.js.map