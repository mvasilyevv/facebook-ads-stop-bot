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
const hard_reload_js_1 = require("./hard-reload.js");
const creator_service_js_1 = require("./creator-service.js");
const service_js_1 = require("./meta-api/service.js");
const service_js_2 = require("./ad-library/service.js");
const am_fetch_js_1 = require("./am/am-fetch.js");
const am_config_js_1 = require("./am/am-config.js");
const PORT = process.env.GRPC_PORT ? parseInt(process.env.GRPC_PORT, 10) : 50051;
const sessionManager = new session_manager_js_1.SessionManager();
const SESSION_STATUS_HEARTBEAT_MS = 5_000;
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
        // Self-heal Layer 1: если primary-вкладку Ads Manager закрыли, но браузер жив —
        // переоткрываем её на known-good/реконструированном URL кабинета (чужие вкладки не трогаем).
        // Если браузер/CDP мертвы — бросит 'Основная страница браузера недоступна' → эскалация
        // на observer (reconnect/StartBrowser, Layer 2).
        const fallbackUrl = (0, am_fetch_js_1.reconstructAdsManagerUrl)(req.session_id);
        const page = await sessionManager.ensureAdsManagerPage(session, {
            fallbackUrl: fallbackUrl ?? undefined,
        });
        // --- am_tabular режим (active replication): метрики из graph-канала UI, без DOM/скролла. ---
        // am_tabular — живой REST → данные ВСЕГДА актуальны, reload для данных НЕ нужен. Токен сниффим
        // один раз (acquireGraphContext кэширует по session_id); reload бывает только при cache-miss
        // или протухании токена (code 190 → re-sniff + retry).
        const amStart = Date.now();
        const campaignIds = Array.isArray(req.campaign_ids) ? req.campaign_ids : [];
        const amConfig = (0, am_config_js_1.defaultAmConfig)(campaignIds, req.owner_tag || '');
        let acquired = await (0, am_fetch_js_1.acquireGraphContext)(page, req.session_id);
        let result = await (0, am_fetch_js_1.runAmScanWithContext)(page, acquired.ctx, amConfig);
        if (result.diagnostics.authExpired) {
            console.warn('[scan][am] access_token протух (190) → re-sniff + retry');
            (0, am_fetch_js_1.invalidateGraphContext)(req.session_id);
            acquired = await (0, am_fetch_js_1.acquireGraphContext)(page, req.session_id, { forceRefresh: true });
            result = await (0, am_fetch_js_1.runAmScanWithContext)(page, acquired.ctx, amConfig);
        }
        const d = result.diagnostics;
        console.log(`[scan][am] sniffed=${acquired.sniffed} scope=${d.scopeCampaignCount}` +
            `${d.ownerResolved ? '(owner)' : ''} ads_metrics=${d.adCountMetrics} ` +
            `ads_names=${d.adCountNames} names=${d.namesResolved} status=${d.statusResolved} ` +
            `edgeOnly=${d.adsEdgeOnly} metricsOnly=${d.metricsOnly} ` +
            `amError=${d.amError ?? '-'} nameError=${d.nameError ?? '-'}`);
        if (d.adsEdgeOnly > 0) {
            console.warn(`[scan][am] ВНИМАНИЕ: ${d.adsEdgeOnly} ад'ов есть в ads-edge, но нет в am_tabular: `
                + d.adsEdgeOnlySample.join(','));
        }
        console.log(`[scan][am] campaigns=${d.campaigns.length}`);
        const amWarnings = [];
        if (d.amError)
            amWarnings.push('am_tabular_error');
        if (d.nameError || d.namesResolved === 0)
            amWarnings.push('am_names_missing');
        if (d.adsEdgeOnly > 0)
            amWarnings.push(`am_edge_only:${d.adsEdgeOnly}`);
        const amProtoRows = result.rows.map(toProtoRow);
        const amDuration = (Date.now() - amStart) / 1000;
        call.write({
            session_id: req.session_id,
            complete: {
                all_rows: amProtoRows,
                total_passes: 1,
                duration_seconds: amDuration,
                dismissed_modals: [],
                unknown_modal_artifacts: [],
                phase_timings: {
                    refresh_ms: 0,
                    first_row_ms: 0,
                    scroll_ms: 0,
                    parse_ms: 0,
                    total_ms: Math.round(amDuration * 1000),
                },
                partial_row_ids: [],
                warnings: amWarnings,
                empty_reason: amProtoRows.length === 0 ? 'no_active_ads' : '',
                rows_with_all_metrics_empty: result.rows.filter((r) => !r.impressions && !Number(r.spend || 0) && !r.cpm && !r.cpc && !r.ctr).length,
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
// --- Вспомогательные функции ---
function toProtoRow(row) {
    return {
        fb_ad_id: row.fb_ad_id,
        campaign_id: row.campaign_id,
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
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
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
async function listCampaignsHandler(call, callback) {
    try {
        // Берём активную ads-сессию observer'а (с кешированным graph-токеном), а не создаём
        // новую — у свежей сессии нет истории запросов и токен не извлекался.
        const session = sessionManager.getPreferredSession();
        const page = getPage(session);
        const campaigns = await (0, am_fetch_js_1.listOwnerCampaigns)(page, call.request.owner_tag ?? '', session.id);
        callback(null, { campaigns });
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
        hardReloadPage: hardReloadPageHandler,
        listCampaigns: listCampaignsHandler,
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
        uploadImage: metaApiHandlers.uploadImage,
        uploadVideo: metaApiHandlers.uploadVideo,
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