import type { Page } from 'playwright';

export interface InPageAbortBinding {
  dispose: () => void;
}

/**
 * Abort every fetch registered under ``operationId`` inside the browser page.
 *
 * The cancelled marker is intentionally written even when no controller is
 * registered yet. This closes the race where gRPC is cancelled immediately
 * before the first page.evaluate starts and creates its AbortController.
 */
export async function abortInPageFetches(page: Page, operationId: string): Promise<void> {
  try {
    await page.evaluate((id: string) => {
      const root = globalThis as typeof globalThis & {
        __fbAgentFetchAbort?: {
          controllers: Map<string, Set<AbortController>>;
          cancelled: Set<string>;
        };
      };
      const state = root.__fbAgentFetchAbort ??= {
        controllers: new Map<string, Set<AbortController>>(),
        cancelled: new Set<string>(),
      };
      state.cancelled.add(id);
      for (const controller of state.controllers.get(id) ?? []) {
        controller.abort('grpc_cancelled');
      }
    }, operationId);
  } catch {
    // The page/context may already be gone. The caller still observes its local
    // AbortSignal and classifies the external result conservatively.
  }
}

/** Remove the browser-side cancellation tombstone after an operation settles. */
export async function clearInPageFetchOperation(page: Page, operationId: string): Promise<void> {
  try {
    await page.evaluate((id: string) => {
      const root = globalThis as typeof globalThis & {
        __fbAgentFetchAbort?: {
          controllers: Map<string, Set<AbortController>>;
          cancelled: Set<string>;
        };
      };
      const state = root.__fbAgentFetchAbort;
      if (!state) return;
      state.controllers.delete(id);
      state.cancelled.delete(id);
    }, operationId);
  } catch {
    // Best effort cleanup; page destruction also releases the registry.
  }
}

/**
 * Connect a Node AbortSignal (gRPC cancellation/deadline) to the browser-side
 * controllers. Returns a disposer that removes the Node listener.
 */
export function bindAbortSignalToPage(
  page: Page,
  operationId: string,
  signal: AbortSignal | undefined,
): InPageAbortBinding {
  if (!signal) return { dispose: () => undefined };

  const onAbort = (): void => {
    void abortInPageFetches(page, operationId);
  };
  signal.addEventListener('abort', onAbort, { once: true });
  if (signal.aborted) onAbort();
  return {
    dispose: () => signal.removeEventListener('abort', onAbort),
  };
}

/** Reject promptly while a Playwright wait is still pending. */
export async function raceWithAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) throw new Error('browser operation cancelled');

  let onAbort: (() => void) | undefined;
  const cancelled = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(new Error('browser operation cancelled'));
    signal.addEventListener('abort', onAbort, { once: true });
  });
  try {
    return await Promise.race([promise, cancelled]);
  } finally {
    if (onAbort) signal.removeEventListener('abort', onAbort);
  }
}
