// gRPC-обработчики CreatorService: RunPlan (стрим), StartRecording, StopRecording,
// GetRecorderStatus. Вся логика выполнения/записи живёт в browser-bundle
// (window.__fbAgent), а здесь — только мост между gRPC и page.evaluate().
import * as grpc from '@grpc/grpc-js';
import type { BrowserContext, Frame, Page } from 'playwright';
import { SessionManager } from './session-manager.js';
import { findPreferredPrimaryPage } from './session-manager.js';
import {
  addCreatorEventListener,
  injectCreator,
  type CreatorEventListener,
} from './creator-injector.js';
import type { BrowserSession } from './types.js';

const CHECKPOINT_MARKERS = ['/checkpoint/', 'checkpoint?next=', 'security/checkpoint'];

function grpcCodeForError(err: any): number {
  const message = String(err?.message || '').toLowerCase();
  return message.includes('not found') || message.includes('не найден')
    ? grpc.status.NOT_FOUND
    : grpc.status.INTERNAL;
}

function getPage(session: BrowserSession): Page {
  const preferredPage = findPreferredPrimaryPage(session.browser);
  if (preferredPage && preferredPage !== session.primaryPage) {
    session.primaryPage = preferredPage;
  }
  const page = session.primaryPage;
  const closed = typeof page?.isClosed === 'function' && page.isClosed();
  if (!page || closed) {
    throw new Error('Основная страница браузера недоступна');
  }
  return page;
}

function getContextFromPage(page: Page): BrowserContext {
  // Playwright Page.context() есть всегда у реального CDP-подключения.
  return page.context();
}

function isCheckpointUrl(url: string | null | undefined): boolean {
  const normalized = String(url || '').toLowerCase();
  return CHECKPOINT_MARKERS.some((marker) => normalized.includes(marker));
}

function safeParseJson<T>(raw: string, fallback: T): T {
  try {
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function createCreatorServiceHandlers(sessionManager: SessionManager) {
  function resolveSession(sessionId: string): BrowserSession {
    const normalizedSessionId = String(sessionId || '').trim();
    return normalizedSessionId
      ? sessionManager.getSession(normalizedSessionId)
      : sessionManager.getPreferredSession();
  }

  async function prepareCreator(sessionId: string): Promise<{ session: BrowserSession; page: Page; context: BrowserContext }> {
    const session = resolveSession(sessionId);
    const page = getPage(session);
    const context = getContextFromPage(page);
    // Инъекция идемпотентна — повторный вызов на том же контексте — no-op.
    await injectCreator(context);
    return { session, page, context };
  }

  async function startRecording(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const { page } = await prepareCreator(req.session_id);
      const planName = String(req.plan_name || '').trim() || 'unnamed';
      await page.evaluate((name: string) => {
        const api = (window as any).__fbAgent;
        if (!api || typeof api.startRecording !== 'function') {
          throw new Error('__fbAgent.startRecording не доступен в странице');
        }
        return api.startRecording(name);
      }, planName);
      callback(null, { started: true, message: `Запись начата: ${planName}` });
    } catch (err: any) {
      callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось начать запись' });
    }
  }

  async function stopRecording(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const { page } = await prepareCreator(req.session_id);
      const result = await page.evaluate(() => {
        const api = (window as any).__fbAgent;
        if (!api || typeof api.stopRecording !== 'function') {
          throw new Error('__fbAgent.stopRecording не доступен в странице');
        }
        return api.stopRecording();
      });
      const planJson = JSON.stringify({
        schema_version: 1,
        plan_name: result?.planName ?? '',
        steps: Array.isArray(result?.steps) ? result.steps : [],
      });
      const recordedSteps = Array.isArray(result?.steps) ? result.steps.length : 0;
      callback(null, { stopped: true, plan_json: planJson, recorded_steps: recordedSteps });
    } catch (err: any) {
      callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось остановить запись' });
    }
  }

  async function getRecorderStatus(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const { page } = await prepareCreator(req.session_id);
      const status = await page.evaluate(() => {
        const api = (window as any).__fbAgent;
        if (!api || typeof api.getRecorderStatus !== 'function') {
          return { active: false, planName: '', recordedSteps: 0 };
        }
        return api.getRecorderStatus();
      });
      callback(null, {
        recording: Boolean(status?.active),
        plan_name: String(status?.planName || ''),
        recorded_steps: Number(status?.recordedSteps || 0),
      });
    } catch (err: any) {
      callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось получить статус recorder' });
    }
  }

  async function runPlan(call: any): Promise<void> {
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;
    let frameNavHandler: ((frame: Frame) => void) | null = null;
    let attachedPage: Page | null = null;

    const safeWrite = (event: any): boolean => {
      if (cancelled || call.destroyed || call.writableEnded) return false;
      try {
        call.write(event);
        return true;
      } catch {
        return false;
      }
    };

    const endIfActive = () => {
      if (!call.destroyed && !call.writableEnded) {
        try {
          call.end();
        } catch {
          // Закрытие может уже произойти со стороны клиента — это не критично.
        }
      }
    };

    const cleanup = () => {
      if (unsubscribe) {
        try { unsubscribe(); } catch { /* noop */ }
        unsubscribe = null;
      }
      if (frameNavHandler && attachedPage) {
        try { attachedPage.off('framenavigated', frameNavHandler); } catch { /* noop */ }
      }
      frameNavHandler = null;
      attachedPage = null;
    };

    call.on('cancelled', () => { cancelled = true; cleanup(); });
    call.on('close', () => { cancelled = true; cleanup(); });
    call.on('error', () => { cancelled = true; cleanup(); });

    const startedAt = Date.now();
    let totalSteps = 0;
    let stepIndex = -1;

    try {
      const req = call.request;
      const { page, context } = await prepareCreator(req.session_id);
      attachedPage = page;

      const plan = safeParseJson<any>(String(req.plan_json || ''), null);
      const variables = safeParseJson<Record<string, unknown>>(String(req.variables_json || ''), {});
      if (!plan || !Array.isArray(plan.steps)) {
        throw new Error('plan_json пустой или не содержит steps');
      }
      totalSteps = plan.steps.length;

      const listener: CreatorEventListener = (event, payload) => {
        if (cancelled) return;
        const p = (payload || {}) as Record<string, any>;
        const tsMs = Date.now();
        if (event === 'step_started') {
          stepIndex += 1;
          safeWrite({
            started: {
              step: String(p.step || ''),
              index: stepIndex,
              timestamp_ms: tsMs,
            },
          });
        } else if (event === 'step_finished') {
          safeWrite({
            finished: {
              step: String(p.step || ''),
              index: stepIndex,
              timestamp_ms: tsMs,
              detail_json: p.detail ? JSON.stringify(p.detail) : '',
            },
          });
        } else if (event === 'step_failed') {
          safeWrite({
            failed: {
              step: String(p.step || ''),
              index: stepIndex,
              error: String(p.error || ''),
              timestamp_ms: tsMs,
            },
          });
        } else if (event === 'step_skipped') {
          safeWrite({
            skipped: {
              step: String(p.step || ''),
              index: stepIndex,
              reason: String(p.reason || ''),
              timestamp_ms: tsMs,
            },
          });
        }
      };
      unsubscribe = addCreatorEventListener(context, listener);

      frameNavHandler = (frame: Frame) => {
        if (cancelled) return;
        if (frame !== page.mainFrame()) return;
        const url = frame.url();
        if (isCheckpointUrl(url)) {
          safeWrite({
            checkpoint: { url, detail: 'FB checkpoint detected' },
          });
        }
      };
      page.on('framenavigated', frameNavHandler);

      const result = await page.evaluate(
        (args: { plan: any; vars: Record<string, unknown> }) => {
          const api = (window as any).__fbAgent;
          if (!api || typeof api.run !== 'function') {
            return { ok: false, error: '__fbAgent.run не доступен в странице' };
          }
          return api.run(args.plan, args.vars);
        },
        { plan, vars: variables },
      );

      safeWrite({
        complete: {
          ok: Boolean(result?.ok),
          error: String(result?.error || ''),
          total_steps: totalSteps,
          duration_ms: Date.now() - startedAt,
        },
      });
    } catch (err: any) {
      safeWrite({
        complete: {
          ok: false,
          error: err?.message || 'Ошибка выполнения плана',
          total_steps: totalSteps,
          duration_ms: Date.now() - startedAt,
        },
      });
    } finally {
      cleanup();
      endIfActive();
    }
  }

  return {
    runPlan,
    startRecording,
    stopRecording,
    getRecorderStatus,
  };
}
