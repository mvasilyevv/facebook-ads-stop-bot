import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import * as path from 'path';
import * as fs from 'fs';
import {
  createHash,
  createHmac,
  randomUUID,
  timingSafeEqual,
} from 'crypto';
import type { ServiceDefinition } from '@grpc/grpc-js';
import {
  SessionManager,
  closeForeignCabinetTabs,
} from './session-manager.js';
import { createMetaApiServiceHandlers } from './meta-api/service.js';
import {
  acquireGraphContext,
  invalidateGraphContext,
  listOwnerCampaigns,
  reconstructAdsManagerUrl,
  runAmScanWithContext,
} from './am/am-fetch.js';
import {
  findIncompleteScanRowIds,
  METRICS_CONTRACT_REVISION,
} from './am/am-completeness.js';
import { defaultAmConfig } from './am/am-config.js';
import { withPageRoleLock } from './page-lock.js';
import { isNetworkFetchError, recordFetchOutcome, shouldHealNow } from './session-health.js';
import { bindGrpcDeadlineAbort } from './grpc-deadline.js';
import { startBrowserAgentMetricsServer, type BrowserAgentMetricsServer } from './metrics.js';
import {
  consumeMaintenanceCapability,
  MaintenanceCapabilityAuthorityUnavailableError,
  MaintenanceCapabilityConsumeDeniedError,
} from './maintenance-authority-client.js';

const PORT = process.env.GRPC_PORT ? parseInt(process.env.GRPC_PORT, 10) : 50051;
const sessionManager = new SessionManager();
const MAINTENANCE_CAPABILITY_MAX_TTL_SECONDS = 35;

function isSafeAuthorityEndpoint(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
    );
  } catch {
    return false;
  }
}

export function validateBrowserCapabilitySecrets(
  environment: Record<string, string | undefined> = process.env,
): void {
  const maintenance = String(
    environment.BROWSER_MAINTENANCE_CAPABILITY_SECRET || '',
  );
  const operations = [
    environment.BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE,
    environment.BROWSER_OPERATION_CAPABILITY_SECRET_META_API,
    environment.BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR,
  ].map((value) => String(value || ''));
  const authorityToken = String(
    environment.BROWSER_AUTHORITY_CONSUMER_TOKEN || '',
  );
  const authorityUrl = String(
    environment.BROWSER_AUTHORITY_CONSUME_URL || '',
  );
  const maintenanceAuthorityUrl = String(
    environment.BROWSER_MAINTENANCE_CONSUME_URL || '',
  );
  const valid = (value: string): boolean =>
    /^[A-Za-z0-9_-]{48,}$/.test(value);
  const secrets = [maintenance, ...operations, authorityToken];
  if (
    secrets.some((value) => !valid(value))
    || !isSafeAuthorityEndpoint(authorityUrl)
    || !isSafeAuthorityEndpoint(maintenanceAuthorityUrl)
    || authorityUrl === maintenanceAuthorityUrl
  ) {
    throw new Error('Browser capability secrets are unavailable');
  }
  if (new Set(secrets).size !== secrets.length) {
    throw new Error('Browser capability secrets must be independently scoped');
  }
}

export function verifyMaintenanceCapabilitySignature(
  request: Record<string, unknown>,
  options: { nowSeconds?: number; secret?: string } = {},
): void {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1_000);
  const secret = options.secret
    ?? process.env.BROWSER_MAINTENANCE_CAPABILITY_SECRET
    ?? '';
  const profileId = String(request.vision_profile_id || '');
  const visionApiUrl = String(request.vision_api_url || '');
  const visionFolderId = String(request.vision_folder_id || '');
  const visionToken = String(request.vision_x_token || '');
  const maintenanceOwner = String(request.maintenance_owner || '');
  const expiresAt = Number(request.capability_expires_at);
  const nonce = String(request.capability_nonce || '');
  const signature = String(request.capability_signature || '');

  if (secret.length < 48) {
    throw new Error('Browser maintenance capability secret is unavailable');
  }
  if (!profileId || profileId.includes('\n') || profileId.includes('\r')) {
    throw new Error('Canonical Vision profile is invalid');
  }
  if (
    !visionToken
    || !visionApiUrl
    || visionApiUrl.includes('\n')
    || visionApiUrl.includes('\r')
    || visionFolderId.includes('\n')
    || visionFolderId.includes('\r')
  ) {
    throw new Error('Canonical Vision recovery credentials are invalid');
  }
  if (!/^[0-9a-f]{32}$/.test(maintenanceOwner)) {
    throw new Error('Valid browser maintenance owner is required');
  }
  if (
    !Number.isSafeInteger(expiresAt)
    || expiresAt <= nowSeconds
    || expiresAt > nowSeconds + MAINTENANCE_CAPABILITY_MAX_TTL_SECONDS
  ) {
    throw new Error('Browser maintenance capability is expired or unbounded');
  }
  if (!/^[0-9a-f]{32}$/.test(nonce) || !/^[0-9a-f]{64}$/.test(signature)) {
    throw new Error('Browser maintenance capability is malformed');
  }
  const payload = [
    'recover_browser_profile/v1',
    profileId,
    maintenanceOwner,
    String(expiresAt),
    nonce,
    visionApiUrl,
    visionFolderId,
    createHash('sha256').update(visionToken).digest('hex'),
  ].join('\n');
  const expected = Buffer.from(
    createHmac('sha256', secret).update(payload).digest('hex'),
    'hex',
  );
  const provided = Buffer.from(signature, 'hex');
  if (
    expected.length !== provided.length
    || !timingSafeEqual(expected, provided)
  ) {
    throw new Error('Browser maintenance capability signature is invalid');
  }
}

function lifecycleAbort(
  call: any,
  capabilityExpiresAtSeconds?: number,
): {
  controller: AbortController;
  dispose: () => void;
} {
  const controller = new AbortController();
  const disposeDeadline = bindGrpcDeadlineAbort(call, controller);
  const onCancelled = () => controller.abort('grpc_cancelled');
  call.on('cancelled', onCancelled);
  const capabilityTimer = Number.isSafeInteger(capabilityExpiresAtSeconds)
    ? setTimeout(
      () => controller.abort('capability_expired'),
      Math.max(0, Number(capabilityExpiresAtSeconds) * 1_000 - Date.now()),
    )
    : undefined;
  return {
    controller,
    dispose: () => {
      disposeDeadline();
      if (capabilityTimer !== undefined) clearTimeout(capabilityTimer);
      call.removeListener?.('cancelled', onCancelled);
    },
  };
}

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
  return message.includes('not found') || message.includes('не найден') ? grpc.status.NOT_FOUND : grpc.status.INTERNAL;
}

// --- Обработчики BrowserSessionService ---

async function startBrowser(call: any, callback: any) {
  const abort = lifecycleAbort(call);
  try {
    const req = call.request;
    const session = await sessionManager.startBrowser({
      visionXToken: req.vision_x_token,
      visionApiUrl: req.vision_api_url || 'http://127.0.0.1:3030',
      visionProfileId: req.vision_profile_id,
      visionFolderId: req.vision_folder_id || undefined,
      viewportWidth: req.viewport_width || 1280,
      viewportHeight: req.viewport_height || 800,
      signal: abort.controller.signal,
    });

    if (!abort.controller.signal.aborted) callback(null, {
      session_id: session.id,
      profile: {
        folder_id: session.visionFolderId,
        profile_id: session.visionProfileId,
        cdp_port: session.cdpPort,
      },
      initial_page_url: session.primaryPage?.url() || '',
    });
  } catch (err: any) {
    if (!abort.controller.signal.aborted) callback({
      code: grpc.status.INTERNAL,
      message: err.message || 'Не удалось запустить браузер',
    });
  } finally {
    abort.dispose();
  }
}

export function createReconnectBrowserHandler(manager: SessionManager) {
  return async function reconnectBrowser(call: any, callback: any) {
    const abort = lifecycleAbort(call);
    try {
      const session = await manager.reconnectBrowser(
        String(call.request.session_id || ''),
        { signal: abort.controller.signal },
      );

      if (!abort.controller.signal.aborted) callback(null, {
        session_id: session.id,
        profile: {
          folder_id: session.visionFolderId,
          profile_id: session.visionProfileId,
          cdp_port: session.cdpPort,
        },
        initial_page_url: '',
      });
    } catch (err: any) {
      const code = grpcCodeForError(err);
      if (!abort.controller.signal.aborted) callback({ code, message: err.message });
    } finally {
      abort.dispose();
    }
  };
}

export function createRecoverBrowserProfileHandler(
  manager: SessionManager,
  dependencies: {
    verify?: typeof verifyMaintenanceCapabilitySignature;
    consume?: typeof consumeMaintenanceCapability;
  } = {},
) {
  const verify = dependencies.verify ?? verifyMaintenanceCapabilitySignature;
  const consume = dependencies.consume ?? consumeMaintenanceCapability;
  return async function recoverBrowserProfileUnderMaintenance(call: any, callback: any) {
    try {
      verify(call.request);
    } catch (err: any) {
      callback({
        code: grpc.status.PERMISSION_DENIED,
        message: String(err?.message || 'Browser maintenance capability rejected'),
      });
      return;
    }
    // Authorization must remain live for the full lifecycle mutation. Expiry
    // cancels authority, Vision HTTP and CDP work server-side even if the
    // caller leaves the transport open.
    const abort = lifecycleAbort(call, Number(call.request.capability_expires_at));
    try {
      const req = call.request;
      // The HMAC is not a durable replay boundary. Vision lifecycle mutation is
      // permitted only after PostgreSQL commits the single consume for this
      // exact active maintenance owner.
      await consume(req, { signal: abort.controller.signal });
      const session = await manager.recoverBrowserProfileUnderMaintenance({
        visionXToken: req.vision_x_token,
        visionApiUrl: req.vision_api_url || 'http://127.0.0.1:3030',
        visionProfileId: req.vision_profile_id,
        visionFolderId: req.vision_folder_id || undefined,
      }, abort.controller.signal);

      if (!abort.controller.signal.aborted) callback(null, {
        session_id: session.id,
        profile: {
          folder_id: session.visionFolderId,
          profile_id: session.visionProfileId,
          cdp_port: session.cdpPort,
        },
        initial_page_url: session.primaryPage?.url() || '',
      });
    } catch (err: any) {
      if (!abort.controller.signal.aborted) {
        const code = err instanceof MaintenanceCapabilityConsumeDeniedError
          ? grpc.status.PERMISSION_DENIED
          : err instanceof MaintenanceCapabilityAuthorityUnavailableError
            ? grpc.status.UNAVAILABLE
            : grpcCodeForError(err);
        callback({
          code,
          message: String(err?.message || 'Browser maintenance recovery failed'),
        });
      }
    } finally {
      abort.dispose();
    }
  };
}

const reconnectBrowser = createReconnectBrowserHandler(sessionManager);
const recoverBrowserProfileUnderMaintenance =
  createRecoverBrowserProfileHandler(sessionManager);

// Фаза "подготовка рабочего места": открыть вкладки кабинетов перед сканом.
// Per-cabinet, идемпотентно: заранее готовим физически разные
// scan/control вкладки. Money-вызов никогда не использует scan page.
// Ошибка одного кабинета не валит остальные — собираем per-cabinet результаты.
async function openCabinetTabs(call: any, callback: any) {
  const abort = lifecycleAbort(call);
  try {
    const session = sessionManager.getSession(call.request.session_id);
    const actIds: string[] = call.request.ad_account_ids || [];
    const results = [];
    for (const actId of actIds) {
      if (abort.controller.signal.aborted) {
        throw new Error('browser operation cancelled');
      }
      try {
        const scanPage = await withPageRoleLock(session.id, 'scan', actId, () =>
          sessionManager.ensureScanPage(session, {
            actId,
            signal: abort.controller.signal,
          }),
        );
        const controlPage = await withPageRoleLock(session.id, 'control', actId, () =>
          sessionManager.ensureControlPage(session, {
            actId,
            signal: abort.controller.signal,
          }),
        );
        if (scanPage === controlPage) {
          throw new Error('scan/control page isolation violated');
        }
        results.push({
          ad_account_id: actId,
          opened: true,
          url: scanPage.url(),
          error: '',
        });
      } catch (e: any) {
        if (abort.controller.signal.aborted) {
          throw e;
        }
        results.push({
          ad_account_id: actId,
          opened: false,
          url: '',
          error: e?.message || String(e),
        });
      }
    }
    // Гигиена вкладок: после открытия нужных кабинетов закрываем кабинетные вкладки вне
    // набора офферов (напр. дефолтный кабинет профиля) — чтобы не копить лишние. Только при
    // непустом наборе и best-effort (ошибка cleanup не валит результат открытия).
    if (
      actIds.length > 0
      && session.browser
      && !abort.controller.signal.aborted
    ) {
      try {
        const closed = await closeForeignCabinetTabs(session.browser, actIds);
        if (closed > 0) {
          console.warn(`[openCabinetTabs] закрыто кабинетных вкладок вне офферов: ${closed}`);
        }
      } catch (e: any) {
        console.warn(`[openCabinetTabs] cleanup вкладок не удался: ${e?.message || e}`);
      }
    }
    if (!abort.controller.signal.aborted) callback(null, { results });
  } catch (err: any) {
    const code = grpcCodeForError(err);
    if (!abort.controller.signal.aborted) callback({ code, message: err.message });
  } finally {
    abort.dispose();
  }
}

// --- Обработчики ScannerService ---

async function runScanCycle(call: any) {
  const req = call.request;
  const abortController = new AbortController();
  const disposeDeadlineAbort = bindGrpcDeadlineAbort(call, abortController);
  let cancelled = abortController.signal.aborted;
  const onAbort = () => {
    cancelled = true;
  };
  abortController.signal.addEventListener('abort', onAbort, { once: true });
  call.on('cancelled', () => {
    cancelled = true;
    abortController.abort('grpc_cancelled');
  });
  call.on('close', () => {
    cancelled = true;
    abortController.abort('grpc_closed');
  });

  const endIfActive = () => {
    if (!call.destroyed && !call.writableEnded) {
      call.end();
    }
  };

  try {
    const actId = String(req.ad_account_id || '')
      .replace(/^act_/, '')
      .trim();
    if (!/^\d+$/.test(actId)) {
      throw new Error('ad_account_id обязателен и должен быть числовым');
    }
    const session = sessionManager.getSession(req.session_id);
    // A closed account-scoped scan page may be recreated while the existing
    // browser connection is still live. This does not change the Vision
    // profile lifecycle. A dead browser/CDP fails closed and is handled only
    // by the exclusive maintenance path.
    const fallbackUrl = reconstructAdsManagerUrl(req.session_id, actId);
    // --- am_tabular режим (active replication): метрики из graph-канала UI, без DOM/скролла. ---
    // am_tabular — живой REST → данные ВСЕГДА актуальны, reload для данных НЕ нужен. Токен сниффим
    // один раз (acquireGraphContext кэширует по session_id); reload бывает только при cache-miss
    // или протухании токена (code 190 → re-sniff + retry).
    const amStart = Date.now();
    const campaignIds: string[] = Array.isArray(req.campaign_ids) ? req.campaign_ids : [];
    const amConfig = defaultAmConfig(campaignIds, req.owner_tag || '');
    const operationId = `scan:${req.session_id}:${actId}:${randomUUID()}`;
    // Сканы одного кабинета сериализованы между собой, но control page имеет
    // другой lock-key и другой execution context.
    const scan = await withPageRoleLock(req.session_id, 'scan', actId, async () => {
      const page = await sessionManager.ensureScanPage(session, {
        fallbackUrl,
        actId,
        signal: abortController.signal,
      });
      let acquired = await acquireGraphContext(page, req.session_id, {
        expectedActId: actId,
        signal: abortController.signal,
      });
      let result = await runAmScanWithContext(page, acquired.ctx, amConfig, {
        signal: abortController.signal,
        operationId,
      });
      // Разлогин/чекпоинт: re-sniff токена бессмыслен (сессия протухла) — не тратим
      // reload, отдаём результат с loginRequired наверх (observer поднимет алерт).
      if (result.diagnostics.authExpired && !result.diagnostics.loginRequired) {
        console.warn('[scan][am] access_token протух (190) → re-sniff + retry');
        invalidateGraphContext(req.session_id, actId);
        acquired = await acquireGraphContext(page, req.session_id, {
          forceRefresh: true,
          expectedActId: actId,
          signal: abortController.signal,
        });
        result = await runAmScanWithContext(page, acquired.ctx, amConfig, {
          signal: abortController.signal,
          operationId,
        });
      }

      // Recovery is part of this scan operation: keep the same role lock and
      // finish the reload before publishing the stream completion. This avoids
      // post-completion browser work and prevents the next scan from crossing
      // the failed page's recovery.
      const diagnostics = result.diagnostics;
      const netFail = (
        isNetworkFetchError(diagnostics.amError)
        || isNetworkFetchError(diagnostics.nameError)
      );
      recordFetchOutcome(session, !netFail);
      if (netFail && shouldHealNow(session, Date.now())) {
        invalidateGraphContext(req.session_id, actId);
        const healed = await sessionManager.reloadPageAfterNetworkFailureWithinRoleLock(
          req.session_id,
          {
            role: 'scan',
            actId,
            page,
            signal: abortController.signal,
          },
        );
        console.warn(`[scan][am] page reload: ${healed.action} (ok=${healed.ok})`);
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
      console.warn(
        `[scan][am] ВНИМАНИЕ: ${d.adsEdgeOnly} ад'ов есть в ads-edge, но нет в am_tabular: ` +
          d.adsEdgeOnlySample.join(','),
      );
    }
    console.log(`[scan][am] campaigns=${d.campaigns.length}`);
    const amWarnings: string[] = [];
    if (d.loginRequired) amWarnings.push('am_login_required');
    if (d.amError) amWarnings.push('am_tabular_error');
    if (d.nameError || d.namesResolved === 0) amWarnings.push('am_names_missing');
    if (d.adsEdgeOnly > 0) amWarnings.push(`am_edge_only:${d.adsEdgeOnly}`);
    const partialRowIds = findIncompleteScanRowIds(result.rows);
    if (partialRowIds.length > 0) {
      console.warn(
        `[scan][am] incomplete row identity/hierarchy/metrics: ${partialRowIds.slice(0, 12).join(',')}`,
      );
    }
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
        partial_row_ids: partialRowIds,
        warnings: amWarnings,
        // Разлогин/чекпоинт (money-критично) имеет приоритет над «нет активных ад'ов»:
        // пустой скан из-за протухшей сессии Vision — это НЕ «нет рекламы», а слепота
        // канала. Гоним отдельный маркер, чтобы observer поднял алерт «нужен ре-логин»,
        // а не тихо ушёл в IDLE как при обычном пустом кабинете.
        empty_reason: d.loginRequired ? 'login_required' : amProtoRows.length === 0 ? 'no_active_ads' : '',
        rows_with_all_metrics_empty: result.rows.filter(
          (r: any) => !r.impressions && !Number(r.spend || 0) && !r.cpm && !r.cpc && !r.ctr,
        ).length,
        metrics_contract_revision: METRICS_CONTRACT_REVISION,
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
  } finally {
    disposeDeadlineAbort();
    abortController.signal.removeEventListener('abort', onAbort);
  }
}

// --- Вспомогательные функции ---

function toProtoRow(row: any): any {
  return {
    fb_ad_id: row.fb_ad_id,
    campaign_id: row.campaign_id,
    adset_id: row.adset_id,
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

async function listCampaignsHandler(call: any, callback: any) {
  const abort = lifecycleAbort(call);
  try {
    const req = call.request;
    const sessionId = String(req.session_id || '').trim();
    if (!sessionId) {
      callback({
        code: grpc.status.INVALID_ARGUMENT,
        message: 'session_id обязателен',
      });
      return;
    }
    const actId = String(req.ad_account_id || '')
      .replace(/^act_/, '')
      .trim();
    if (!/^\d+$/.test(actId)) {
      callback({
        code: grpc.status.INVALID_ARGUMENT,
        message: 'ad_account_id обязателен и должен быть числовым',
      });
      return;
    }
    // Use only the exact process-local session established by this client.
    // A preferred-session fallback could cross a Vision profile/config revision.
    const session = sessionManager.getSession(sessionId);
    const fallbackUrl = reconstructAdsManagerUrl(session.id, actId);
    const page = await withPageRoleLock(session.id, 'scan', actId, () =>
      sessionManager.ensureScanPage(session, {
        fallbackUrl,
        actId,
        signal: abort.controller.signal,
      }),
    );
    const campaigns = await withPageRoleLock(session.id, 'scan', actId, () =>
      listOwnerCampaigns(
        page,
        req.owner_tag ?? '',
        session.id,
        actId,
        abort.controller.signal,
      ),
    );
    if (!abort.controller.signal.aborted) callback(null, { campaigns });
  } catch (err: any) {
    if (!abort.controller.signal.aborted) {
      callback({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    }
  } finally {
    abort.dispose();
  }
}

// --- Запуск сервера ---

function main() {
  // A socket-open process with unusable authorization would be false-green.
  // Refuse to bind either gRPC or metrics until both trust-boundary secrets are
  // present and independently scoped.
  validateBrowserCapabilitySecrets();
  // Лимит сообщения 50 МБ (как у Python-клиента в core/meta_api/client.py): дефолт
  // grpc-js — 4 МБ, из-за чего видео-чанк ровно 4 МБ + накладные protobuf
  // (4194354 > 4194304) и картинки до 8 МБ (unary UploadImage) отвергались как
  // RESOURCE_EXHAUSTED на шаге creating/uploading залива.
  const server = new grpc.Server({
    'grpc.max_receive_message_length': 50 * 1024 * 1024,
    'grpc.max_send_message_length': 50 * 1024 * 1024,
  });

  // Загружаем proto-описания сервисов.
  const browserSessionProto = loadProto('browser_session.proto') as any;
  const scannerProto = loadProto('scanner.proto') as any;
  const metaApiProto = loadProto('meta_api.proto') as any;

  const browserSessionService = browserSessionProto.fb_agent.browser_session.v1.BrowserSessionService;
  const scannerService = scannerProto.fb_agent.scanner.v1.ScannerService;
  const metaApiService = metaApiProto.fb_agent.meta_api.v1.MetaApiService;

  server.addService(browserSessionService.service, {
    startBrowser,
    reconnectBrowser,
    recoverBrowserProfileUnderMaintenance,
    openCabinetTabs,
  });

  server.addService(scannerService.service, {
    runScanCycle,
    listCampaigns: listCampaignsHandler,
  });

  const metaApiHandlers = createMetaApiServiceHandlers(sessionManager);
  server.addService(metaApiService.service, {
    executeGraphCallV5: metaApiHandlers.executeGraphCallV5,
    checkMetaApiHealth: metaApiHandlers.checkMetaApiHealth,
    uploadImage: metaApiHandlers.uploadImage,
    uploadVideo: metaApiHandlers.uploadVideo,
  });

  server.bindAsync(`0.0.0.0:${PORT}`, grpc.ServerCredentials.createInsecure(), async (error, port) => {
    if (error) {
      console.error(`Не удалось запустить gRPC-сервер: ${error.message}`);
      process.exit(1);
    }
    let metricsServer: BrowserAgentMetricsServer;
    try {
      metricsServer = await startBrowserAgentMetricsServer();
    } catch (metricsError) {
      console.error('Не удалось запустить metrics-сервер Browser Agent:', metricsError);
      server.forceShutdown();
      process.exit(1);
      return;
    }
    // Явно держим event loop живым: в detached-запуске gRPC server может не удержать процесс сам.
    const keepAliveTimer = setInterval(() => undefined, 60_000);

    const shutdown = () => {
      clearInterval(keepAliveTimer);
      metricsServer.close().finally(() => {
        server.tryShutdown(() => process.exit(0));
      });
    };
    process.once('SIGINT', shutdown);
    process.once('SIGTERM', shutdown);
    console.log(`Browser Agent слушает gRPC :${port} и metrics :${metricsServer.port}`);
    server.start();
  });
}

if (require.main === module) {
  main();
}
