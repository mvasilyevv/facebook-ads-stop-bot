import { afterEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { abortInPageFetches, clearInPageFetchOperation } from './in-page-abort.js';

type AbortRegistry = {
  controllers: Map<string, Set<AbortController>>;
  cancelled: Set<string>;
};

const registryRoot = globalThis as typeof globalThis & { __fbAgentFetchAbort?: AbortRegistry };

const executingPage = {
  evaluate: async (fn: (arg: string) => unknown, arg: string) => fn(arg),
} as any;

afterEach(() => {
  delete registryRoot.__fbAgentFetchAbort;
});

describe('browser-side Graph abort registry', () => {
  it('gRPC cancellation aborts an already running in-page controller', async () => {
    const controller = new AbortController();
    registryRoot.__fbAgentFetchAbort = {
      controllers: new Map([['op-1', new Set([controller])]]),
      cancelled: new Set(),
    };

    await abortInPageFetches(executingPage, 'op-1');

    assert.equal(controller.signal.aborted, true);
    assert.equal(registryRoot.__fbAgentFetchAbort.cancelled.has('op-1'), true);
  });

  it('cancellation tombstone closes the abort-before-controller race', async () => {
    await abortInPageFetches(executingPage, 'op-race');
    const controller = new AbortController();
    const state = registryRoot.__fbAgentFetchAbort!;

    if (state.cancelled.has('op-race')) controller.abort('grpc_cancelled');

    assert.equal(controller.signal.aborted, true);
    await clearInPageFetchOperation(executingPage, 'op-race');
    assert.equal(state.cancelled.has('op-race'), false);
  });
});
