// gRPC-обработчики MetaApiService.
// Мост между gRPC-запросом и executeGraphCall/checkMetaApiHealth в client.ts.
import * as grpc from '@grpc/grpc-js';
import { createHash, randomUUID } from 'crypto';
import type { Page } from 'playwright';
import {
  SessionManager,
  extractAdAccountId,
  findLiveAdsManagerPage,
} from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { executeGraphCall, checkMetaApiHealth, type GraphApiCallParams } from './client.js';
import { uploadImage, uploadVideoSingle } from './upload.js';
import { withPageRoleLock } from '../page-lock.js';
import {
  assertPageEpochUnchanged,
  beginPageEpoch,
  PageEpochChangedError,
} from '../page-epoch.js';
import {
  isTokenRejectedGraphError,
  recordFetchOutcome,
  shouldHealNow,
  shouldReloadForStaleToken,
} from '../session-health.js';
import {
  assertCanonicalGraphMethodSemantics,
  BROWSER_OPERATION_CONTRACT_VERSION,
  graphOperationBinding,
  mediaOperationBinding,
  verifyOperationCapability,
  type OperationCapabilityBinding,
} from './operation-capability.js';
import { consumeOperationCapability } from './operation-authority-client.js';
import {
  assertGraphOperationOwnership,
  type GraphOwnershipPreflightOptions,
} from './ownership.js';

// v5 removes URL-backed image upload and requires capability-bound bytes.
// It retains exact task/query/body semantics for every controlled operation.
export const BROWSER_CONTRACT_VERSION = BROWSER_OPERATION_CONTRACT_VERSION;

export function grpcCodeForError(err: any): number {
  const message = String(err?.message || '').toLowerCase();
  if (message.includes('capability consume was denied')) {
    // A durable row may already have been consumed by a process that died
    // after crossing the boundary. Upstream must reconcile UNKNOWN, never
    // convert this replay into a proven rejection.
    return grpc.status.ABORTED;
  }
  if (
    message.includes('operation capability')
    || message.includes('caller is not authorized')
    || message.includes('ownership preflight rejected')
    || message.includes('graph method override')
    || message.includes('graph request method semantics')
    || message.includes('graph get body semantics')
    || message.includes('graph endpoint query/fragment semantics')
  ) {
    return grpc.status.PERMISSION_DENIED;
  }
  if (message.includes('ownership preflight')) {
    return grpc.status.FAILED_PRECONDITION;
  }
  if (message.includes('cabinet_backoff')) {
    // Вкладка не открывалась: попытка придержана после подряд идущих отказов.
    // Наружу ничего не ушло — это отказ ДО отправки, как и остальные пути,
    // на которых вкладка кабинета не открылась.
    return grpc.status.FAILED_PRECONDITION;
  }
  if (message.includes('page_epoch_changed')) {
    // Страница навигировала до отправки: грант не списан, fetch не уходил.
    // Это доказанный отказ ДО внешней границы, а не потерянный ответ.
    return grpc.status.FAILED_PRECONDITION;
  }
  if (
    message.includes('cabinet_not_found')
    || message.includes('cabinet_not_confirmed')
  ) {
    // Вкладка кабинета не открылась. Все три текста рождаются только в
    // ensureRolePage, то есть строго ДО первого обращения к Meta: страница —
    // это то, ЧЕРЕЗ что мутация отправляется, и без неё отправлять нечем.
    // INTERNAL здесь означал бы AmbiguousResultError, то есть «требуется ручная
    // сверка» после залива, в котором наружу не ушло ни одного запроса.
    return grpc.status.FAILED_PRECONDITION;
  }
  if (message.includes('cabinet_login_required')) {
    // Профиль разлогинен: вкладка кабинета не открылась, до Meta не дошли и не
    // дойдём, пока человек не войдёт. Это отказ ДО отправки, а не потерянный
    // ответ — INTERNAL здесь означал бы UNKNOWN, и каждый залив под разлогином
    // оставлял бы «требуется ручная сверка» там, где ничего не отправлялось.
    return grpc.status.FAILED_PRECONDITION;
  }
  if (message.includes('authority is unavailable')) {
    return grpc.status.UNAVAILABLE;
  }
  if (message.includes('exact session/profile identity')) {
    return grpc.status.FAILED_PRECONDITION;
  }
  return message.includes('not found') || message.includes('не найден')
    ? grpc.status.NOT_FOUND
    : grpc.status.INTERNAL;
}

function normalizeActId(value: unknown): string {
  const normalized = String(value || '').replace(/^act_/, '').trim();
  return /^\d+$/.test(normalized) ? normalized : '';
}

function actIdFromEndpoint(endpoint: string): string {
  return endpoint.match(/(?:^|\/)act_(\d+)(?:\/|$)/)?.[1] ?? '';
}

function bindGrpcAbort(call: any): {
  controller: AbortController;
  bindCapabilityExpiry: (expiresAtSeconds: number) => void;
  dispose: () => void;
} {
  const controller = new AbortController();
  const onCancelled = () => controller.abort('grpc_cancelled');
  const onClose = () => controller.abort('grpc_closed');
  call.on('cancelled', onCancelled);
  call.on('close', onClose);
  const remainingMs = remainingDeadlineMs(call);
  const deadlineTimer = remainingMs === undefined
    ? undefined
    : setTimeout(() => controller.abort('grpc_deadline_exceeded'), remainingMs);
  let capabilityTimer: ReturnType<typeof setTimeout> | undefined;
  return {
    controller,
    bindCapabilityExpiry: (expiresAtSeconds: number) => {
      if (capabilityTimer !== undefined) clearTimeout(capabilityTimer);
      if (!Number.isSafeInteger(expiresAtSeconds) || expiresAtSeconds <= 0) {
        controller.abort('capability_invalid');
        return;
      }
      capabilityTimer = setTimeout(
        () => controller.abort('capability_expired'),
        Math.max(0, expiresAtSeconds * 1_000 - Date.now()),
      );
    },
    dispose: () => {
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      if (capabilityTimer !== undefined) clearTimeout(capabilityTimer);
      call.removeListener?.('cancelled', onCancelled);
      call.removeListener?.('close', onClose);
    },
  };
}

function grpcAbortError(signal: AbortSignal): {
  code: number;
  message: string;
} {
  const reason = String(signal.reason || 'grpc_cancelled');
  if (reason === 'capability_invalid') {
    return {
      code: grpc.status.PERMISSION_DENIED,
      message: 'Browser operation capability is invalid',
    };
  }
  if (reason === 'capability_expired') {
    return {
      code: grpc.status.DEADLINE_EXCEEDED,
      message: 'Browser operation capability expired',
    };
  }
  if (reason === 'grpc_deadline_exceeded') {
    return {
      code: grpc.status.DEADLINE_EXCEEDED,
      message: 'Browser operation gRPC deadline exceeded',
    };
  }
  return {
    code: grpc.status.CANCELLED,
    message: 'Browser operation was cancelled',
  };
}

function createUnaryOperationResponder(
  callback: any,
  signal: AbortSignal,
): {
  respond: (error: any, response?: any) => void;
  dispose: () => void;
} {
  let responded = false;
  const respond = (error: any, response?: any): void => {
    if (responded) return;
    responded = true;
    callback(error, response);
  };
  const onAbort = (): void => respond(grpcAbortError(signal));
  signal.addEventListener('abort', onAbort, { once: true });
  return {
    respond,
    dispose: () => signal.removeEventListener('abort', onAbort),
  };
}

function remainingDeadlineMs(call: any): number | undefined {
  const raw = call.getDeadline?.();
  const deadlineMs = raw instanceof Date ? raw.getTime() : Number(raw);
  if (!Number.isFinite(deadlineMs)) return undefined;
  return Math.max(0, deadlineMs - Date.now());
}

function isMoneyControlGraphCall(
  method: string,
  endpoint: string,
  queryParams: Record<string, string>,
): boolean {
  const normalizedMethod = method.toUpperCase();
  if (normalizedMethod !== 'GET') return true;
  const normalizedEndpoint = endpoint.trim();
  if (/^\/?\d+\/thumbnails$/.test(normalizedEndpoint)) return true;
  if (!/^\/?\d+$/.test(normalizedEndpoint)) return false;
  const fields = new Set(
    String(queryParams.fields || '')
      .split(',')
      .map((field) => field.trim().toLowerCase())
      .filter(Boolean),
  );
  return fields.has('status') || fields.has('effective_status');
}

// Dependency injection is test-only (no real browser/Meta calls). Production
// always uses the concrete SessionManager and upload implementations.
export interface MetaApiServiceDeps {
  uploadImage?: typeof uploadImage;
  uploadVideoSingle?: typeof uploadVideoSingle;
  checkMetaApiHealth?: typeof checkMetaApiHealth;
  getControlPage?: (
    session: BrowserSession,
    actId: string,
    signal?: AbortSignal,
  ) => Page | Promise<Page>;
  getInteractivePage?: (
    session: BrowserSession,
    actId: string,
    signal?: AbortSignal,
  ) => Page | Promise<Page>;
  verifyOperationCapability?: (
    request: Record<string, unknown>,
    binding: OperationCapabilityBinding,
  ) => void;
  consumeOperationCapability?: (
    request: Record<string, unknown>,
    binding: OperationCapabilityBinding,
    signal?: AbortSignal,
  ) => Promise<void> | void;
  assertGraphOperationOwnership?: (
    page: Page,
    params: GraphApiCallParams,
    expectedAccountId: string,
    options?: GraphOwnershipPreflightOptions,
  ) => Promise<void>;
  poisonRolePage?: (
    session: BrowserSession,
    role: 'control' | 'interactive',
    actId: string,
    page: Page,
  ) => void;
  /**
   * Compatibility injection for focused tests. Production never supplies it:
   * local signature verification and durable consume remain separate stages.
   */
  authorizeOperationCapability?: (
    request: Record<string, unknown>,
    binding: OperationCapabilityBinding,
    signal?: AbortSignal,
  ) => Promise<void> | void;
}

export function createMetaApiServiceHandlers(
  sessionManager: SessionManager,
  deps: MetaApiServiceDeps = {},
) {
  const _uploadImage = deps.uploadImage ?? uploadImage;
  const _uploadVideoSingle = deps.uploadVideoSingle ?? uploadVideoSingle;
  const _checkMetaApiHealth = deps.checkMetaApiHealth ?? checkMetaApiHealth;
  const _verifyOperationCapability = deps.verifyOperationCapability
    ?? (deps.authorizeOperationCapability
      ? (() => undefined)
      : verifyOperationCapability);
  const _consumeOperationCapability = deps.consumeOperationCapability
    ?? deps.authorizeOperationCapability
    ?? (async (
      request: Record<string, unknown>,
      binding: OperationCapabilityBinding,
      signal?: AbortSignal,
    ) => {
      await consumeOperationCapability(request, binding, { signal });
    });
  const _assertGraphOperationOwnership = deps.assertGraphOperationOwnership
    ?? (deps.authorizeOperationCapability
      ? (async () => undefined)
      : assertGraphOperationOwnership);
  const _poisonRolePage = deps.poisonRolePage
    ?? ((
      session: BrowserSession,
      role: 'control' | 'interactive',
      actId: string,
      page: Page,
    ) => (sessionManager as SessionManager & {
      poisonRolePage?: typeof sessionManager.poisonRolePage;
    }).poisonRolePage?.(session, role, actId, page));
  const _getControlPage = deps.getControlPage
    ?? ((session: BrowserSession, actId: string, signal?: AbortSignal) =>
      sessionManager.ensureControlPage(session, {
        actId: actId || undefined,
        signal,
      }));
  const _getInteractivePage = deps.getInteractivePage
    ?? ((session: BrowserSession, actId: string, signal?: AbortSignal) =>
      sessionManager.ensureInteractivePage(session, {
        actId: actId || undefined,
        signal,
      }));

  function resolveSession(sessionId: string): BrowserSession {
    const normalizedSessionId = String(sessionId || '').trim();
    return normalizedSessionId
      ? sessionManager.getSession(normalizedSessionId)
      : sessionManager.getPreferredSession();
  }

  function resolveExactOperationSession(request: Record<string, unknown>): BrowserSession {
    const sessionId = String(request.session_id || '').trim();
    const profileId = String(request.vision_profile_id || '').trim();
    if (!sessionId || !profileId) {
      throw new Error('Browser operation requires exact session/profile identity');
    }
    let session: BrowserSession;
    try {
      session = sessionManager.getSession(sessionId);
    } catch {
      throw new Error('Browser operation requires exact session/profile identity');
    }
    if (session.visionProfileId !== profileId) {
      throw new Error('Browser operation requires exact session/profile identity');
    }
    return session;
  }

  function resolveHealthSession(
    sessionId: string,
    expectedVisionProfileId: string,
  ): BrowserSession {
    const normalizedSessionId = String(sessionId || '').trim();
    const normalizedProfileId = String(expectedVisionProfileId || '').trim();
    if (normalizedSessionId) {
      const session = sessionManager.getSession(normalizedSessionId);
      if (normalizedProfileId && session.visionProfileId !== normalizedProfileId) {
        throw new Error(
          `Session ${normalizedSessionId} does not own Vision profile ${normalizedProfileId}`,
        );
      }
      return session;
    }
    return normalizedProfileId
      ? sessionManager.getSessionForVisionProfile(normalizedProfileId)
      : sessionManager.getPreferredSession();
  }

  async function executeGraphCallV5Handler(call: any, callback: any): Promise<void> {
    const grpcAbort = bindGrpcAbort(call);
    const responder = createUnaryOperationResponder(
      callback,
      grpcAbort.controller.signal,
    );
    try {
      const req = call.request;
      const endpoint = String(req.endpoint || '/me');
      const requestedAccount = String(req.ad_account_id || '').trim();
      const actId = normalizeActId(req.ad_account_id) || actIdFromEndpoint(endpoint);
      if (requestedAccount && !normalizeActId(requestedAccount)) {
        responder.respond({
          code: grpc.status.INVALID_ARGUMENT,
          message: 'ad_account_id must be an explicit numeric account id',
        });
        return;
      }

      // Конвертация proto map<string, string> в plain object для page.evaluate.
      const queryParams: Record<string, string> = {};
      if (req.query_params && typeof req.query_params === 'object') {
        for (const [key, value] of Object.entries(req.query_params)) {
          queryParams[String(key)] = String(value);
        }
      }
      const requestMethod = String(req.method || 'GET').trim().toUpperCase();
      const requestBody = String(req.body_json || '');
      assertCanonicalGraphMethodSemantics(
        requestMethod,
        endpoint,
        queryParams,
        requestBody,
      );

      const requestedTimeoutMs = req.timeout_ms && req.timeout_ms > 0 ? req.timeout_ms : 30_000;
      const remainingMs = remainingDeadlineMs(call);
      if (remainingMs !== undefined && remainingMs <= 0) {
        responder.respond({
          code: grpc.status.DEADLINE_EXCEEDED,
          message: 'Graph deadline exhausted',
        });
        return;
      }
      const timeoutMs = remainingMs === undefined
        ? requestedTimeoutMs
        : Math.max(1, Math.min(requestedTimeoutMs, Math.floor(remainingMs)));
      const params: GraphApiCallParams = {
        method: requestMethod as 'GET' | 'POST' | 'DELETE',
        endpoint,
        queryParams,
        bodyJson: requestBody.length > 0 ? requestBody : undefined,
        timeoutMs,
      };

      const moneyControl = (
        isMoneyControlGraphCall(params.method, endpoint, queryParams)
        || String(req.authorized_caller || '').trim() === 'campaign_creator'
      );
      if (moneyControl && !normalizeActId(req.ad_account_id)) {
        responder.respond({
          code: grpc.status.INVALID_ARGUMENT,
          message: 'money Graph call requires explicit ad_account_id',
        });
        return;
      }
      const session = moneyControl
        ? resolveExactOperationSession(req)
        : resolveSession(req.session_id);
      const capabilityBinding: OperationCapabilityBinding | undefined = moneyControl
        ? {
            browserContractVersion: BROWSER_CONTRACT_VERSION,
            rpc: 'execute_graph_call',
            operation: graphOperationBinding(
              params.method,
              endpoint,
              queryParams,
              requestBody,
            ),
            sessionId: session.id,
            visionProfileId: session.visionProfileId,
            adAccountId: actId,
          }
        : undefined;
      if (moneyControl) {
        _verifyOperationCapability(req, capabilityBinding!);
        grpcAbort.bindCapabilityExpiry(Number(req.capability_expires_at));
      }
      const role = moneyControl ? 'control' : 'interactive';
      const operationId = `${role}:${session.id}:${actId || 'default'}:${randomUUID()}`;
      const result = await withPageRoleLock(session.id, role, actId, async () => {
        let operationPage: Page | undefined;
        try {
          operationPage = moneyControl
            ? await _getControlPage(session, actId, grpcAbort.controller.signal)
            : await _getInteractivePage(session, actId, grpcAbort.controller.signal);
          // Снимок эпохи берётся сразу после выбора страницы: всё, что случится
          // с ней дальше — до списания гранта и до отправки — обязано стать
          // отказом, а не неоднозначным исходом.
          const pageEpochSnapshot = beginPageEpoch(operationPage);
          if (moneyControl) {
            // Ownership is proven in the same page/lock that will send the
            // mutation. Only after that read succeeds do we atomically consume
            // the PostgreSQL grant and cross the external-send boundary.
            await _assertGraphOperationOwnership(
              operationPage,
              params,
              actId,
              {
                signal: grpcAbort.controller.signal,
                operationId,
                // Подпись capability уже проверена выше, поэтому вызывающий и
                // кабинет здесь — доказанные факты, а не поля запроса.
                capability: {
                  caller: String(req.authorized_caller || '').trim(),
                  adAccountId: capabilityBinding!.adAccountId,
                },
              },
            );
            // Грант списывается необратимо, поэтому эпоха проверяется ДО него:
            // навигация, случившаяся во время чтения владения, не должна стоить
            // одноразового гранта.
            assertPageEpochUnchanged(operationPage, pageEpochSnapshot);
            await _consumeOperationCapability(
              req,
              capabilityBinding!,
              grpcAbort.controller.signal,
            );
          }
          const graphResult = await executeGraphCall(operationPage, params, {
            signal: grpcAbort.controller.signal,
            operationId,
            assertBeforeDispatch: moneyControl
              ? () => assertPageEpochUnchanged(operationPage!, pageEpochSnapshot)
              : undefined,
          });

          // Keep failed fetch recovery inside the same role lock. Cancellation
          // instead poisons the page below so recovery cannot wait on bad CDP.
          const netFail = graphResult.statusCode === 0;
          recordFetchOutcome(session, !netFail);
          if (
            !grpcAbort.controller.signal.aborted
            && netFail
            && shouldHealNow(session, Date.now())
          ) {
            const healed = await sessionManager.reloadPageAfterNetworkFailureWithinRoleLock(
              session.id,
              {
                role,
                actId,
                page: operationPage,
                signal: grpcAbort.controller.signal,
              },
            );
            console.warn(
              `[meta-api] page reload after network failure: ${healed.action} (ok=${healed.ok})`,
            );
          }

          // Токен читается из HTML вкладки на каждом вызове, поэтому после
          // ре-логина оператора вкладка держит дологиновый рендер и канал
          // остаётся мёртвым (прод 18.08.2026: 4.5 часа плюс ручной ensure-cdp).
          // Перечитываем страницу и повторяем ОДИН раз — только чтение: повтор
          // денежной мутации остаётся решением money-пути, а не побочным
          // эффектом лечения вкладки. При настоящем разлогине повтор вернёт тот
          // же отказ, и его увидит инцидент «нужен вход».
          if (
            !grpcAbort.controller.signal.aborted
            && !netFail
            && !moneyControl
            && params.method.toUpperCase() === 'GET'
            && isTokenRejectedGraphError(graphResult.error)
            && shouldReloadForStaleToken(session, Date.now())
          ) {
            const healed = await sessionManager.reloadPageAfterNetworkFailureWithinRoleLock(
              session.id,
              {
                role,
                actId,
                page: operationPage,
                signal: grpcAbort.controller.signal,
              },
            );
            console.warn(
              `[meta-api] page reload after token rejection: ${healed.action} (ok=${healed.ok})`,
            );
            if (healed.ok && !grpcAbort.controller.signal.aborted) {
              return await executeGraphCall(operationPage, params, {
                signal: grpcAbort.controller.signal,
                operationId,
              });
            }
          }
          return graphResult;
        } catch (error) {
          // Навигировавшая money-страница непригодна навсегда: её execution
          // context уже другой, а reload лечит не то — он сам навигация. Лечение
          // здесь только одно: выбросить вкладку, следующий вызов создаст свою.
          if (operationPage && error instanceof PageEpochChangedError) {
            _poisonRolePage(session, role, actId, operationPage);
            operationPage = undefined;
          }
          throw error;
        } finally {
          if (operationPage && grpcAbort.controller.signal.aborted) {
            _poisonRolePage(
              session,
              role,
              actId,
              operationPage,
            );
          }
        }
      }, { signal: grpcAbort.controller.signal });

      // The transport is gone. Never turn an aborted/closed gRPC into a false
      // confirmed response; upstream keeps the external outcome UNKNOWN.
      if (grpcAbort.controller.signal.aborted) return;

      responder.respond(null, {
        status_code: result.statusCode,
        response_json: result.responseJson,
        duration_ms: result.durationMs,
        error: result.error
          ? {
              code: result.error.code,
              subcode: result.error.subcode,
              type: result.error.type,
              message: result.error.message,
              fbtrace_id: result.error.fbtraceId,
            }
          : undefined,
      });
    } catch (err: any) {
      if (grpcAbort.controller.signal.aborted) return;
      responder.respond({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    } finally {
      responder.dispose();
      grpcAbort.dispose();
    }
  }

  async function checkMetaApiHealthHandler(call: any, callback: any): Promise<void> {
    const grpcAbort = bindGrpcAbort(call);
    try {
      const req = call.request;
      const session = resolveHealthSession(
        req.session_id,
        req.expected_vision_profile_id,
      );
      // Кабинет пробы называет вызывающий. Адрес текущей вкладки — случайное
      // состояние браузера, а не identity: раньше проба брала act отсюда и
      // каждые 2 секунды воскрешала вкладку произвольного кабинета, которую
      // оператор не мог закрыть (прод, 17.08.2026).
      const requestedAct = String(req.ad_account_id || '').trim();
      if (requestedAct && !/^\d{1,32}$/.test(requestedAct)) {
        throw new Error('ad_account_id must be 1..32 digits');
      }
      const fullProbe = Boolean(req.full_probe);
      const reusablePage = requestedAct
        ? null
        : findLiveAdsManagerPage(session.browser ?? null);

      if (!requestedAct && !reusablePage) {
        // Без явного кабинета проба ничего не открывает: отсутствие живой
        // вкладки Ads Manager — честный отказ, а не повод создать вкладку.
        if (grpcAbort.controller.signal.aborted) return;
        callback(null, {
          healthy: false,
          current_url: '',
          token_present: false,
          token_length: 0,
          detail: 'no_ads_manager_page',
          probe_performed: false,
          probe_ok: false,
          probe_status_code: 0,
          probe_duration_ms: 0,
          probe_detail: 'not_performed',
          browser_contract_version: BROWSER_CONTRACT_VERSION,
          session_id: session.id,
          vision_profile_id: session.visionProfileId,
        });
        return;
      }

      const lockAct = requestedAct || extractAdAccountId(reusablePage?.url?.()) || '';

      const result = await withPageRoleLock(session.id, 'interactive', lockAct, async () => {
        const page = requestedAct
          ? await _getInteractivePage(
            session,
            requestedAct,
            grpcAbort.controller.signal,
          )
          : (reusablePage as any);
        return _checkMetaApiHealth(page, {
          fullProbe,
          signal: grpcAbort.controller.signal,
        });
      }, { signal: grpcAbort.controller.signal });

      if (grpcAbort.controller.signal.aborted) return;
      callback(null, {
        healthy: result.healthy,
        current_url: result.currentUrl,
        token_present: result.tokenPresent,
        token_length: result.tokenLength,
        detail: result.detail,
        probe_performed: result.probePerformed,
        probe_ok: result.probeOk,
        probe_status_code: result.probeStatusCode,
        probe_duration_ms: result.probeDurationMs,
        probe_detail: result.probeDetail,
        browser_contract_version: BROWSER_CONTRACT_VERSION,
        session_id: session.id,
        vision_profile_id: session.visionProfileId,
      });
    } catch (err: any) {
      if (grpcAbort.controller.signal.aborted) return;
      // Если сессия не найдена — возвращаем healthy=false как штатный ответ
      // (а не gRPC ошибку), потому что вызывающий health_watchdog хочет видеть состояние.
      callback(null, {
        healthy: false,
        current_url: '',
        token_present: false,
        token_length: 0,
        detail: `error: ${String(err?.message ?? err)}`,
        probe_performed: false,
        probe_ok: false,
        probe_status_code: 0,
        probe_duration_ms: 0,
        probe_detail: 'not_performed',
        browser_contract_version: BROWSER_CONTRACT_VERSION,
        session_id: '',
        vision_profile_id: '',
      });
    } finally {
      grpcAbort.dispose();
    }
  }

  async function uploadImageHandler(call: any, callback: any): Promise<void> {
    const grpcAbort = bindGrpcAbort(call);
    const responder = createUnaryOperationResponder(
      callback,
      grpcAbort.controller.signal,
    );
    try {
      const req = call.request;
      const actId = normalizeActId(req.ad_account_id);
      if (!actId) {
        responder.respond({
          code: grpc.status.INVALID_ARGUMENT,
          message: 'UploadImage requires explicit ad_account_id',
        });
        return;
      }
      const fileBytes = req.file_bytes;
      // proto-loader отдаёт bytes как Buffer; нормализуем.
      const buf: Buffer = Buffer.isBuffer(fileBytes)
        ? fileBytes
        : Buffer.from(fileBytes || []);

      // Reject a deterministic no-op before consuming its one-shot grant.
      if (buf.length === 0) {
        responder.respond(null, {
          image_hash: '',
          ok: false,
          error: 'INVALID_ARGUMENT: file_bytes пусты',
          url: '',
          duration_ms: 0,
        });
        return;
      }
      const session = resolveExactOperationSession(req);
      const capabilityBinding: OperationCapabilityBinding = {
        browserContractVersion: BROWSER_CONTRACT_VERSION,
        rpc: 'upload_image',
        operation: mediaOperationBinding('upload_image', {
          filename: String(req.filename || ''),
          content_type: String(req.content_type || ''),
          content_sha256: createHash('sha256').update(buf).digest('hex'),
        }),
        sessionId: session.id,
        visionProfileId: session.visionProfileId,
        adAccountId: actId,
      };
      _verifyOperationCapability(req, capabilityBinding);
      grpcAbort.bindCapabilityExpiry(Number(req.capability_expires_at));

      const remainingMs = remainingDeadlineMs(call);
      if (grpcAbort.controller.signal.aborted || remainingMs === 0) return;
      const timeoutMs = remainingMs === undefined ? 120_000 : Math.max(1, Math.min(120_000, remainingMs));
      const operationId = `image:${session.id}:${actId}:${randomUUID()}`;
      const result = await withPageRoleLock(session.id, 'interactive', actId, async () => {
        if (grpcAbort.controller.signal.aborted) {
          return { ok: false, imageHash: '', url: '', error: 'cancelled', durationMs: 0 };
        }
        let page: Page | undefined;
        try {
          page = await _getInteractivePage(
            session,
            actId,
            grpcAbort.controller.signal,
          );
          if (grpcAbort.controller.signal.aborted) {
            return { ok: false, imageHash: '', url: '', error: 'cancelled', durationMs: 0 };
          }
          await _consumeOperationCapability(
            req,
            capabilityBinding,
            grpcAbort.controller.signal,
          );
          return _uploadImage(page, {
            adAccountId: `act_${actId}`,
            filename: String(req.filename || 'upload.jpg'),
            contentType: String(req.content_type || 'image/jpeg'),
            fileBytes: buf,
            timeoutMs,
          }, {
            signal: grpcAbort.controller.signal,
            operationId,
          });
        } finally {
          if (page && grpcAbort.controller.signal.aborted) {
            _poisonRolePage(session, 'interactive', actId, page);
          }
        }
      }, { signal: grpcAbort.controller.signal });

      if (grpcAbort.controller.signal.aborted) return;
      if (!result.ok && result.error.includes('TOKEN_NOT_FOUND_IN_PAGE')) {
        responder.respond({
          code: grpc.status.FAILED_PRECONDITION,
          message: 'Browser operation exact session/profile identity has no Meta token',
        });
        return;
      }

      responder.respond(null, {
        image_hash: result.imageHash,
        ok: result.ok,
        error: result.error,
        url: result.url,
        duration_ms: result.durationMs,
      });
    } catch (err: any) {
      if (grpcAbort.controller.signal.aborted) return;
      responder.respond({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    } finally {
      responder.dispose();
      grpcAbort.dispose();
    }
  }

  // Client streaming: клиент шлёт несколько UploadVideoChunk, сервер отвечает одним
  // UploadVideoResponse. Чанки нужны только для обхода gRPC-лимита сообщения — видео
  // собирается целиком и грузится ОДНИМ multipart-POST (source=File), как картинки.
  // Meta v22 отвергает chunked resumable (upload_phase=start/transfer/finish) как
  // 'Invalid parameter', а single-POST принимает (проверено живьём: 200 + video_id).
  function uploadVideoHandler(call: any, callback: any): void {
    const grpcAbort = bindGrpcAbort(call);
    const t0 = Date.now();
    let chunksProcessed = 0;
    let isFinishing = false;
    let respondedOnce = false;
    let videoId = '';

    // 'data'-обработчик ТОЛЬКО синхронно кладёт chunk в очередь, единственный воркер
    // processChunks разбирает строго последовательно (без гонок async-обработчиков) и
    // накапливает байты по порядку. Раньше async 'data' наезжали → перемешивание/гонка.
    let metadataSeen = false;
    let adAccountId = '';
    let numericActId = '';
    let filename = 'upload.mp4';
    let resolvedSession: BrowserSession | null = null;
    let firstRequest: Record<string, unknown> | null = null;
    const videoBuffers: Buffer[] = [];

    const pendingChunks: any[] = [];
    let processing = false;
    let endReceived = false;
    let isLastChunkSeen = false;

    const cancelUpload = (): void => {
      if (respondedOnce) return;
      respondedOnce = true;
      pendingChunks.length = 0;
      videoBuffers.length = 0;
      grpcAbort.dispose();
      callback(grpcAbortError(grpcAbort.controller.signal));
    };
    grpcAbort.controller.signal.addEventListener('abort', cancelUpload, { once: true });

    function respondError(msg: string): void {
      if (respondedOnce) return;
      if (grpcAbort.controller.signal.aborted) {
        cancelUpload();
        return;
      }
      respondedOnce = true;
      grpcAbort.controller.signal.removeEventListener('abort', cancelUpload);
      grpcAbort.dispose();
      callback(null, {
        video_id: videoId,
        ok: false,
        error: msg,
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    function respondGrpcError(err: unknown): void {
      if (respondedOnce) return;
      if (grpcAbort.controller.signal.aborted) {
        cancelUpload();
        return;
      }
      respondedOnce = true;
      grpcAbort.controller.signal.removeEventListener('abort', cancelUpload);
      grpcAbort.dispose();
      callback({
        code: grpcCodeForError(err),
        message: String((err as { message?: unknown })?.message ?? err),
      });
    }

    function respondSuccess(vid: string): void {
      if (respondedOnce) return;
      if (grpcAbort.controller.signal.aborted) {
        cancelUpload();
        return;
      }
      respondedOnce = true;
      grpcAbort.controller.signal.removeEventListener('abort', cancelUpload);
      grpcAbort.dispose();
      videoId = vid;
      callback(null, {
        video_id: vid,
        ok: true,
        error: '',
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    function bytesOf(chunk: any): Buffer {
      return Buffer.isBuffer(chunk.chunk_bytes)
        ? chunk.chunk_bytes
        : Buffer.from(chunk.chunk_bytes || []);
    }

    async function processChunks(): Promise<void> {
      if (processing || respondedOnce) return;
      processing = true;
      try {
        while (pendingChunks.length > 0) {
          if (grpcAbort.controller.signal.aborted) return;
          const chunk = pendingChunks.shift();
          if (!metadataSeen) {
            adAccountId = String(chunk.ad_account_id || '');
            numericActId = normalizeActId(adAccountId);
            filename = String(chunk.filename || 'upload.mp4');
            if (!numericActId) {
              respondError('Первый chunk должен содержать ad_account_id');
              return;
            }
            adAccountId = `act_${numericActId}`;
            firstRequest = chunk as Record<string, unknown>;
            resolvedSession = resolveExactOperationSession(firstRequest);
            // The full content digest is not available until the stream ends.
            // Bound this unauthenticated phase to the strict RPC TTL; the exact
            // signature is consumed immediately before any browser upload.
            const claimedExpiry = Number(firstRequest.capability_expires_at);
            const boundedExpiry = Number.isSafeInteger(claimedExpiry)
              ? Math.min(claimedExpiry, Math.floor(Date.now() / 1_000) + 185)
              : 0;
            grpcAbort.bindCapabilityExpiry(
              boundedExpiry,
            );
            metadataSeen = true;
          }
          const bytes = bytesOf(chunk);
          if (bytes.length > 0) {
            videoBuffers.push(bytes);
          }
          chunksProcessed += 1;
          if (chunk.is_last_chunk) {
            isLastChunkSeen = true;
          }
        }
        // Очередь пуста: всё видео собрано → грузим ОДНИМ POST.
        if (
          metadataSeen
          && resolvedSession
          && firstRequest
          && !isFinishing
          && (isLastChunkSeen || endReceived)
        ) {
          if (grpcAbort.controller.signal.aborted) return;
          isFinishing = true;
          const full = Buffer.concat(videoBuffers);
          if (full.length === 0) {
            respondError('UploadVideo: пустое видео (0 байт)');
            return;
          }
          const activeSession = resolvedSession;
          const capabilityBinding: OperationCapabilityBinding = {
            browserContractVersion: BROWSER_CONTRACT_VERSION,
            rpc: 'upload_video',
            operation: mediaOperationBinding('upload_video', {
              filename,
              file_size: full.length,
              content_sha256: createHash('sha256').update(full).digest('hex'),
            }),
            sessionId: activeSession.id,
            visionProfileId: activeSession.visionProfileId,
            adAccountId: numericActId,
          };
          _verifyOperationCapability(firstRequest, capabilityBinding);
          const remainingMs = remainingDeadlineMs(call);
          if (remainingMs === 0) return;
          const timeoutMs = remainingMs === undefined ? 120_000 : Math.max(1, Math.min(120_000, remainingMs));
          const operationId = `video:${activeSession.id}:${numericActId}:${randomUUID()}`;
          const res = await withPageRoleLock(activeSession.id, 'interactive', numericActId, async () => {
            if (grpcAbort.controller.signal.aborted) {
              return { ok: false, videoId: '', error: 'cancelled', durationMs: 0 };
            }
            let page: Page | undefined;
            try {
              page = await _getInteractivePage(
                activeSession,
                numericActId,
                grpcAbort.controller.signal,
              );
              if (grpcAbort.controller.signal.aborted) {
                return { ok: false, videoId: '', error: 'cancelled', durationMs: 0 };
              }
              await _consumeOperationCapability(
                firstRequest!,
                capabilityBinding,
                grpcAbort.controller.signal,
              );
              return _uploadVideoSingle(
                page,
                { adAccountId, filename, fileBytes: full, timeoutMs },
                { signal: grpcAbort.controller.signal, operationId },
              );
            } finally {
              if (page && grpcAbort.controller.signal.aborted) {
                _poisonRolePage(activeSession, 'interactive', numericActId, page);
              }
            }
          }, { signal: grpcAbort.controller.signal });
          if (grpcAbort.controller.signal.aborted) return;
          if (res.ok && res.videoId) {
            respondSuccess(res.videoId);
          } else if (res.error.includes('TOKEN_NOT_FOUND_IN_PAGE')) {
            respondGrpcError(
              new Error(
                'Browser operation exact session/profile identity has no Meta token',
              ),
            );
          } else {
            respondError(`UploadVideo: ${res.error || 'no video_id'}`);
          }
        }
      } catch (err: any) {
        respondGrpcError(err);
      } finally {
        processing = false;
      }
      // Догоняем chunks, приехавшие пока обрабатывали/финишили.
      if (!respondedOnce && pendingChunks.length > 0) {
        void processChunks();
      }
    }

    call.on('data', (chunk: any) => {
      if (grpcAbort.controller.signal.aborted || respondedOnce) return;
      pendingChunks.push(chunk);
      void processChunks();
    });

    call.on('end', () => {
      endReceived = true;
      if (!metadataSeen && pendingChunks.length === 0) {
        respondError('UploadVideo: стрим закрыт без единого chunk');
        return;
      }
      void processChunks();
    });

    call.on('error', (err: any) => {
      respondError(`UploadVideo: stream error ${String(err?.message ?? err)}`);
    });

    call.on('cancelled', () => {
      cancelUpload();
    });

    call.on('close', () => {
      if (!grpcAbort.controller.signal.aborted) return;
      cancelUpload();
    });
  }

  return {
    executeGraphCallV5: executeGraphCallV5Handler,
    checkMetaApiHealth: checkMetaApiHealthHandler,
    uploadImage: uploadImageHandler,
    uploadVideo: uploadVideoHandler,
  };
}
