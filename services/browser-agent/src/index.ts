import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import * as path from 'path';
import * as fs from 'fs';
import type { ServiceDefinition } from '@grpc/grpc-js';
import { SessionManager, findPreferredPrimaryPage } from './session-manager.js';
import { hardReloadPage } from './hard-reload.js';
import type { BrowserSession } from './types.js';
import { createCreatorServiceHandlers } from './creator-service.js';
import { createMetaApiServiceHandlers } from './meta-api/service.js';
import { createAdLibraryServiceHandlers } from './ad-library/service.js';
import {
  acquireGraphContext,
  invalidateGraphContext,
  listOwnerCampaigns,
  reconstructAdsManagerUrl,
  runAmScanWithContext,
} from './am/am-fetch.js';
import { defaultAmConfig } from './am/am-config.js';
import { withPageLock } from './page-lock.js';

const PORT = process.env.GRPC_PORT ? parseInt(process.env.GRPC_PORT, 10) : 50051;
const sessionManager = new SessionManager();
const SESSION_STATUS_HEARTBEAT_MS = 5_000;

function loadProto(name: string): grpc.GrpcObject {
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

function grpcCodeForError(err: any): number {
  const message = String(err?.message || '').toLowerCase();
  return message.includes('not found') || message.includes('не найден')
    ? grpc.status.NOT_FOUND
    : grpc.status.INTERNAL;
}

function getPage(session: BrowserSession, pageId?: string): any {
  if (pageId) {
    // Задача на будущее: поддержать несколько страниц, когда появится стабильная привязка page_id.
    throw new Error('Поддержка нескольких страниц пока не реализована');
  }

  const preferredPage = findPreferredPrimaryPage(session.browser);
  if (preferredPage && preferredPage !== session.primaryPage) {
    session.primaryPage = preferredPage;
  }

  const primaryPageClosed = typeof session.primaryPage?.isClosed === 'function' && session.primaryPage.isClosed();
  if (!session.primaryPage || primaryPageClosed) {
    throw new Error('Основная страница браузера недоступна');
  }
  return session.primaryPage;
}

function getSessionForOptionalId(sessionId?: string): BrowserSession {
  const normalizedSessionId = String(sessionId || '').trim();
  return normalizedSessionId
    ? sessionManager.getSession(normalizedSessionId)
    : sessionManager.getPreferredSession();
}

// --- Обработчики BrowserSessionService ---

async function startBrowser(call: any, callback: any) {
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
  } catch (err: any) {
    callback({
      code: grpc.status.INTERNAL,
      message: err.message || 'Не удалось запустить браузер',
    });
  }
}

async function disconnectBrowser(call: any, callback: any) {
  try {
    await sessionManager.disconnectBrowser(call.request.session_id);
    callback(null, {});
  } catch (err: any) {
    callback({ code: grpc.status.NOT_FOUND, message: err.message });
  }
}

async function stopBrowser(call: any, callback: any) {
  try {
    await sessionManager.stopBrowser(call.request.session_id);
    callback(null, {});
  } catch (err: any) {
    callback({ code: grpc.status.NOT_FOUND, message: err.message });
  }
}

async function reconnectBrowser(call: any, callback: any) {
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
  } catch (err: any) {
    const code = grpcCodeForError(err);
    callback({ code, message: err.message });
  }
}

async function getSessionInfo(call: any, callback: any) {
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
  } catch (err: any) {
    callback({ code: grpc.status.NOT_FOUND, message: err.message });
  }
}

async function navigate(call: any, callback: any) {
  try {
    const session = sessionManager.getSession(call.request.session_id);
    const page = getPage(session, call.request.page_id);
    await page.goto(call.request.url, {
      waitUntil: call.request.wait_until || 'domcontentloaded',
    });
    callback(null, { url: page.url() });
  } catch (err: any) {
    const code = grpcCodeForError(err);
    callback({ code, message: err.message });
  }
}

type SessionStatusLookup = (sessionId: string) => BrowserSession;

export function writeSessionStatusEvent(call: any, sessionId: string, lookup: SessionStatusLookup): boolean {
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
  } catch (err: any) {
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

export function streamSessionStatusWithLookup(call: any, lookup: SessionStatusLookup) {
  const sessionId = String(call.request?.session_id || '');
  let closed = false;
  const timer = setInterval(() => {
    if (!writeSessionStatusEvent(call, sessionId, lookup)) {
      closeStream(true);
    }
  }, SESSION_STATUS_HEARTBEAT_MS);

  function closeStream(endCall: boolean) {
    if (closed) return;
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

function streamSessionStatus(call: any) {
  streamSessionStatusWithLookup(call, (sessionId) => sessionManager.getSession(sessionId));
}

// --- Обработчики ScannerService ---

async function runScanCycle(call: any) {
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
    // Мульти-кабинет: числовой ID кабинета из запроса (пусто → legacy одно-кабинетный путь).
    const actId: string | undefined =
      String(req.ad_account_id || '').replace(/^act_/, '').trim() || undefined;
    // Self-heal Layer 1: если primary-вкладку Ads Manager закрыли, но браузер жив —
    // переоткрываем её на known-good/реконструированном URL кабинета (чужие вкладки не трогаем).
    // Если браузер/CDP мертвы — бросит 'Основная страница браузера недоступна' → эскалация
    // на observer (reconnect/StartBrowser, Layer 2).
    const fallbackUrl = reconstructAdsManagerUrl(req.session_id, actId);
    const page = await sessionManager.ensureAdsManagerPage(session, {
      fallbackUrl: fallbackUrl ?? undefined,
      actId,
    });
    // --- am_tabular режим (active replication): метрики из graph-канала UI, без DOM/скролла. ---
    // am_tabular — живой REST → данные ВСЕГДА актуальны, reload для данных НЕ нужен. Токен сниффим
    // один раз (acquireGraphContext кэширует по session_id); reload бывает только при cache-miss
    // или протухании токена (code 190 → re-sniff + retry).
    const amStart = Date.now();
      const campaignIds: string[] = Array.isArray(req.campaign_ids) ? req.campaign_ids : [];
      const amConfig = defaultAmConfig(campaignIds, req.owner_tag || '');
      // H-7 (BA-4): весь цикл работы со страницей (reload для сниффа токена +
      // page.evaluate(fetch) метрик) под per-session локом — чтобы Marketing API
      // mutation (executeGraphCall) не выполнила page.evaluate(fetch) во время
      // нашего reload и наоборот. Иначе «Execution context was destroyed».
      const scan = await withPageLock(req.session_id, async () => {
        let acquired = await acquireGraphContext(page, req.session_id, { expectedActId: actId });
        let result = await runAmScanWithContext(page, acquired.ctx, amConfig);
        if (result.diagnostics.authExpired) {
          console.warn('[scan][am] access_token протух (190) → re-sniff + retry');
          invalidateGraphContext(req.session_id, actId);
          acquired = await acquireGraphContext(page, req.session_id, {
            forceRefresh: true,
            expectedActId: actId,
          });
          result = await runAmScanWithContext(page, acquired.ctx, amConfig);
        }
        return { acquired, result };
      });
      const acquired = scan.acquired;
      const result = scan.result;
      const d = result.diagnostics;
      console.log(
        `[scan][am] sniffed=${acquired.sniffed} scope=${d.scopeCampaignCount}` +
          `${d.ownerResolved ? '(owner)' : ''} ads_metrics=${d.adCountMetrics} ` +
          `ads_names=${d.adCountNames} names=${d.namesResolved} status=${d.statusResolved} ` +
          `edgeOnly=${d.adsEdgeOnly} metricsOnly=${d.metricsOnly} ` +
          `amError=${d.amError ?? '-'} nameError=${d.nameError ?? '-'}`,
      );
      if (d.adsEdgeOnly > 0) {
        console.warn(`[scan][am] ВНИМАНИЕ: ${d.adsEdgeOnly} ад'ов есть в ads-edge, но нет в am_tabular: `
          + d.adsEdgeOnlySample.join(','));
      }
      console.log(`[scan][am] campaigns=${d.campaigns.length}`);
      const amWarnings: string[] = [];
      if (d.amError) amWarnings.push('am_tabular_error');
      if (d.nameError || d.namesResolved === 0) amWarnings.push('am_names_missing');
      if (d.adsEdgeOnly > 0) amWarnings.push(`am_edge_only:${d.adsEdgeOnly}`);
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
          rows_with_all_metrics_empty: result.rows.filter((r: any) => !r.impressions && !Number(r.spend || 0) && !r.cpm && !r.cpc && !r.ctr).length,
        },
      });
      endIfActive();
  } catch (err: any) {
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

function toProtoRow(row: any): any {
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
    // Волна 1: превью крео + метаданные адсета (иначе теряются на TS→proto границе).
    creative_thumb_url: row.creative_thumb_url ?? '',
    creative_image_url: row.creative_image_url ?? '',
    adset_pixel_id: row.adset_pixel_id ?? '',
    adset_daily_budget: row.adset_daily_budget ?? '',
    adset_lifetime_budget: row.adset_lifetime_budget ?? '',
    adset_budget_remaining: row.adset_budget_remaining ?? '',
    adset_learning_stage: row.adset_learning_stage ?? '',
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function hardReloadPageHandler(call: any, callback: any) {
  try {
    const session = sessionManager.getSession(call.request.session_id);
    if (!session) {
      callback({ code: grpc.status.NOT_FOUND, message: 'session not found' });
      return;
    }
    const page = getPage(session, call.request.page_id);
    const bypassCache = call.request.bypass_cache !== false;
    const result = await hardReloadPage(page, bypassCache);
    callback(null, {
      success: result.success,
      error_message: result.errorMessage ?? '',
      reload_ms: result.reloadMs,
    });
  } catch (err: any) {
    callback({ code: grpcCodeForError(err), message: String(err?.message ?? err) });
  }
}

async function listCampaignsHandler(call: any, callback: any) {
  try {
    const req = call.request;
    // Берём активную ads-сессию observer'а (с кешированным graph-токеном), а не создаём
    // новую — у свежей сессии нет истории запросов и токен не извлекался.
    const session = sessionManager.getPreferredSession();
    // Мульти-кабинет (L10): числовой ID кабинета (пусто → legacy primary-вкладка).
    const actId: string | undefined =
      String(req.ad_account_id || '').replace(/^act_/, '').trim() || undefined;
    // actId задан → резолвим/открываем вкладку именно этого кабинета (как runScanCycle),
    // иначе остаёмся на текущей primary-вкладке (старое поведение).
    let page;
    if (actId) {
      const fallbackUrl = reconstructAdsManagerUrl(session.id, actId);
      page = await sessionManager.ensureAdsManagerPage(session, {
        fallbackUrl: fallbackUrl ?? undefined,
        actId,
      });
    } else {
      page = getPage(session);
    }
    // H-7 (BA-4): listOwnerCampaigns внутри может сделать reload (acquireGraphContext
    // cache-miss) — под тем же per-session локом, что и мутации/скан.
    const campaigns = await withPageLock(session.id, () =>
      listOwnerCampaigns(page, req.owner_tag ?? '', session.id),
    );
    callback(null, { campaigns });
  } catch (err: any) {
    callback({ code: grpcCodeForError(err), message: String(err?.message ?? err) });
  }
}

// --- Запуск сервера ---

function main() {
  const server = new grpc.Server();

  // Загружаем proto-описания сервисов.
  const browserSessionProto = loadProto('browser_session.proto') as any;
  const scannerProto = loadProto('scanner.proto') as any;
  const creatorProto = loadProto('creator.proto') as any;
  const metaApiProto = loadProto('meta_api.proto') as any;
  const adLibraryProto = loadProto('ad_library.proto') as any;

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

  const creatorHandlers = createCreatorServiceHandlers(sessionManager);
  server.addService(creatorService.service, {
    runPlan: creatorHandlers.runPlan,
    startRecording: creatorHandlers.startRecording,
    stopRecording: creatorHandlers.stopRecording,
    getRecorderStatus: creatorHandlers.getRecorderStatus,
  });

  const metaApiHandlers = createMetaApiServiceHandlers(sessionManager);
  server.addService(metaApiService.service, {
    executeGraphCall: metaApiHandlers.executeGraphCall,
    checkMetaApiHealth: metaApiHandlers.checkMetaApiHealth,
    uploadImage: metaApiHandlers.uploadImage,
    uploadVideo: metaApiHandlers.uploadVideo,
  });

  const adLibraryHandlers = createAdLibraryServiceHandlers(sessionManager);
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
