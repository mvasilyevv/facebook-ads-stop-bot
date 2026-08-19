import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';

import type { SessionManager } from '../session-manager.js';
import { _resetPageLocks } from '../page-lock.js';
import { BROWSER_CONTRACT_VERSION, createMetaApiServiceHandlers } from './service.js';

function cancellableGraphPage(onStarted: () => void): any {
  let finishGraph: ((value: { status_code: number; response_json: string }) => void) | undefined;
  return {
    waitForFunction: async () => true,
    evaluate: async (_fn: any, args: any) => {
      if (args && typeof args === 'object' && 'endpoint' in args) {
        onStarted();
        return new Promise<{ status_code: number; response_json: string }>((resolve) => {
          finishGraph = resolve;
        });
      }
      if (typeof args === 'string') {
        finishGraph?.({
          status_code: 0,
          response_json: JSON.stringify({
            error: { code: -2, type: 'NetworkError', message: 'cancelled' },
          }),
        });
      }
      return undefined;
    },
  };
}

function unaryCall(request: Record<string, unknown>): EventEmitter & {
  request: Record<string, unknown>;
  getDeadline: () => Date;
} {
  const call = new EventEmitter() as EventEmitter & {
    request: Record<string, unknown>;
    getDeadline: () => Date;
  };
  call.request = request;
  call.getDeadline = () => new Date(Date.now() + 30_000);
  return call;
}

describe('MetaApiService control page cancellation', () => {
  it('publishes the reviewed semantic browser contract version', () => {
    assert.equal(BROWSER_CONTRACT_VERSION, 5);
  });

  it('rejects method override variants before session, capability, or page I/O', async () => {
    let sessionCalls = 0;
    let pageCalls = 0;
    let verifyCalls = 0;
    let ownershipCalls = 0;
    let consumeCalls = 0;
    const manager = {
      getSession: () => {
        sessionCalls += 1;
        return { id: 'session-1', visionProfileId: 'profile-1' };
      },
      getPreferredSession: () => {
        sessionCalls += 1;
        return { id: 'session-1', visionProfileId: 'profile-1' };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getControlPage: () => {
        pageCalls += 1;
        return {} as any;
      },
      getInteractivePage: () => {
        pageCalls += 1;
        return {} as any;
      },
      verifyOperationCapability: () => {
        verifyCalls += 1;
      },
      assertGraphOperationOwnership: async () => {
        ownershipCalls += 1;
      },
      consumeOperationCapability: async () => {
        consumeCalls += 1;
      },
    });
    const variants: Array<{
      endpoint?: string;
      query_params?: Record<string, string>;
      body_json?: string;
    }> = [
      { query_params: { method: 'post' } },
      { query_params: { MeThOd: 'POST' } },
      { query_params: { '%256dethod': 'post' } },
      { query_params: { '%25252525256dethod': 'post' } },
      { query_params: { method: 'get', METHOD: 'post' } },
      { endpoint: '/me?method=post&method=get' },
      { endpoint: '/me%253Fmethod%253Dpost' },
      { endpoint: '/me%25252525253Fmethod%25252525253Dpost' },
      { body_json: '{"method":"post"}' },
      { body_json: '{"\\u006dethod":"post"}' },
      { body_json: '{"%256dethod":"post"}' },
      { body_json: '{"%25252525256dethod":"post"}' },
      { body_json: '{"method":"GET","method":"POST"}' },
      { body_json: 'method%3Dpost' },
    ];

    for (const variant of variants) {
      const error = await new Promise<any>((resolve) => {
        handlers.executeGraphCallV5(
          unaryCall({
            method: 'GET',
            endpoint: variant.endpoint ?? '/me',
            query_params: variant.query_params ?? {},
            body_json: variant.body_json ?? '',
            ad_account_id: '',
            timeout_ms: 30_000,
          }),
          (value: unknown) => resolve(value),
        );
      });
      assert.equal(error.code, 7);
      assert.match(error.message, /method|semantics|query\/fragment/i);
    }
    assert.deepEqual(
      {
        sessionCalls,
        pageCalls,
        verifyCalls,
        ownershipCalls,
        consumeCalls,
      },
      {
        sessionCalls: 0,
        pageCalls: 0,
        verifyCalls: 0,
        ownershipCalls: 0,
        consumeCalls: 0,
      },
    );
  });

  for (const eventName of ['cancelled', 'close'] as const) {
    it(`${eventName} aborts in-page Graph fetch and completes RPC as cancelled`, async () => {
      let startedResolve!: () => void;
      const started = new Promise<void>((resolve) => { startedResolve = resolve; });
      const page = cancellableGraphPage(startedResolve);
      const session = { id: 'session-1', visionProfileId: 'profile-1' };
      const fakeManager = {
        getSession: () => session,
        getPreferredSession: () => session,
      } as unknown as SessionManager;
      const requestedActs: string[] = [];
      const handlers = createMetaApiServiceHandlers(fakeManager, {
        getControlPage: (_session, actId) => {
          requestedActs.push(actId);
          return page;
        },
        authorizeOperationCapability: () => undefined,
      });
      const call = new EventEmitter() as EventEmitter & {
        request: Record<string, unknown>;
        getDeadline: () => Date;
      };
      call.request = {
        session_id: 'session-1',
        vision_profile_id: 'profile-1',
        capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
        ad_account_id: 'act_123',
        method: 'POST',
        endpoint: '/987654321',
        query_params: { status: 'PAUSED' },
        body_json: '{}',
        timeout_ms: 30_000,
      };
      call.getDeadline = () => new Date(Date.now() + 30_000);
      const callbackErrors: any[] = [];

      const pending = handlers.executeGraphCallV5(call, (error: unknown) => {
        callbackErrors.push(error);
      });
      await started;
      call.emit(eventName);
      await pending;

      assert.deepEqual(requestedActs, ['123']);
      assert.equal(callbackErrors.length, 1);
      assert.equal(callbackErrors[0].code, 1);
      assert.match(callbackErrors[0].message, /cancelled/);
    });
  }

  it('capability expiry aborts browser work and completes RPC with a deadline error', async () => {
    let startedResolve!: () => void;
    const started = new Promise<void>((resolve) => { startedResolve = resolve; });
    const page = cancellableGraphPage(startedResolve);
    const session = { id: 'session-1', visionProfileId: 'profile-1' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => page,
      authorizeOperationCapability: () => undefined,
    });
    const call = unaryCall({
      session_id: 'session-1',
      vision_profile_id: 'profile-1',
      capability_expires_at: Math.floor(Date.now() / 1_000),
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '{}',
      timeout_ms: 30_000,
    });
    const callbackResult = new Promise<any>((resolve) => {
      void handlers.executeGraphCallV5(call, (error: unknown) => resolve(error));
    });

    await started;
    const error = await callbackResult;

    assert.equal(error.code, 4);
    assert.match(error.message, /capability expired/);
  });

  it('streaming upload capability expiry completes the RPC instead of hanging', async () => {
    const session = { id: 'session-1', visionProfileId: 'profile-1' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getInteractivePage: () => ({} as any),
      uploadVideoSingle: (async (_page: any, _params: any, options: any) => (
        new Promise((resolve) => {
          options.signal.addEventListener('abort', () => resolve({
            ok: false,
            videoId: '',
            error: 'cancelled',
            durationMs: 0,
          }), { once: true });
        })
      )) as any,
      authorizeOperationCapability: () => undefined,
    });
    const call = new EventEmitter() as EventEmitter & {
      getDeadline: () => Date;
    };
    call.getDeadline = () => new Date(Date.now() + 30_000);
    const callbackResult = new Promise<any>((resolve) => {
      handlers.uploadVideo(call, (error: unknown) => resolve(error));
    });

    call.emit('data', {
      session_id: 'session-1',
      vision_profile_id: 'profile-1',
      capability_expires_at: Math.floor(Date.now() / 1_000),
      ad_account_id: '123',
      filename: 'video.mp4',
      chunk_bytes: Buffer.from('video'),
      is_last_chunk: true,
    });
    call.emit('end');
    const error = await callbackResult;

    assert.equal(error.code, 4);
    assert.match(error.message, /capability expired/);
  });

  for (const request of [
    {
      method: 'POST',
      endpoint: '/act_123/campaigns',
      query_params: {},
    },
    {
      method: 'GET',
      endpoint: '/987654321',
      query_params: { fields: 'id,status' },
    },
    {
      method: 'GET',
      endpoint: '/987654321/thumbnails',
      query_params: { fields: 'uri,is_preferred' },
    },
  ]) {
    it(`${request.method} ${request.endpoint} rejects missing explicit account before session or page use`, async () => {
      const session = { id: 'session-1' };
      let sessionCalls = 0;
      const fakeManager = {
        getSession: () => {
          sessionCalls += 1;
          return session;
        },
        getPreferredSession: () => {
          sessionCalls += 1;
          return session;
        },
      } as unknown as SessionManager;
      let pageCalls = 0;
      const handlers = createMetaApiServiceHandlers(fakeManager, {
        getControlPage: () => {
          pageCalls += 1;
          return {} as any;
        },
      });
      const call = new EventEmitter() as EventEmitter & {
        request: Record<string, unknown>;
        getDeadline: () => Date;
      };
      call.request = {
        session_id: 'session-1',
        ad_account_id: '',
        body_json: '',
        timeout_ms: 30_000,
        ...request,
      };
      call.getDeadline = () => new Date(Date.now() + 30_000);

      const error = await new Promise<any>((resolve) => {
        handlers.executeGraphCallV5(call, (err: unknown) => resolve(err));
      });

      assert.equal(error.code, 3);
      assert.match(error.message, /requires explicit ad_account_id/);
      assert.equal(sessionCalls, 0);
      assert.equal(pageCalls, 0);
    });
  }

  it('keeps failed fetch, recovery reload and callback under one control lock', async () => {
    _resetPageLocks();
    const events: string[] = [];
    let graphCalls = 0;
    let reloadStarted!: () => void;
    const reloadStartedPromise = new Promise<void>((resolve) => {
      reloadStarted = resolve;
    });
    let releaseReload!: () => void;
    const page = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          graphCalls += 1;
          events.push(`graph:${graphCalls}`);
          return graphCalls === 1
            ? {
                status_code: 0,
                response_json: JSON.stringify({
                  error: { code: -2, type: 'NetworkError', message: 'failed' },
                }),
              }
            : { status_code: 200, response_json: '{"success":true}' };
        }
        return undefined;
      },
    };
    const session = {
      id: 'session-lock',
      visionProfileId: 'profile-lock',
      netFailureStreak: 1,
      lastHealAt: new Date(0),
    };
    const manager = {
      getSession: () => session,
      reloadPageAfterNetworkFailureWithinRoleLock: async () => {
        events.push('reload:start');
        reloadStarted();
        await new Promise<void>((resolve) => {
          releaseReload = resolve;
        });
        events.push('reload:end');
        return { action: 'reload', ok: true };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getControlPage: () => page as any,
      authorizeOperationCapability: () => undefined,
    });
    const request = {
      session_id: 'session-lock',
      vision_profile_id: 'profile-lock',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '{}',
      timeout_ms: 30_000,
    };
    const invoke = () => new Promise<any>((resolve, reject) => {
      handlers.executeGraphCallV5(
        unaryCall(request),
        (error: unknown, response: unknown) => (
          error ? reject(error) : resolve(response)
        ),
      );
    });

    const first = invoke();
    await reloadStartedPromise;
    const second = invoke();
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(graphCalls, 1, 'second RPC cannot enter during recovery reload');
    releaseReload();
    const [firstResponse, secondResponse] = await Promise.all([first, second]);

    assert.equal(firstResponse.status_code, 0);
    assert.equal(secondResponse.status_code, 200);
    assert.deepEqual(events, [
      'graph:1',
      'reload:start',
      'reload:end',
      'graph:2',
    ]);
  });

  it('orders local verify, authoritative ownership, durable consume, then mutation', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const page = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          events.push('mutation');
          return { status_code: 200, response_json: '{"success":true}' };
        }
        return undefined;
      },
    };
    const session = { id: 'session-order', visionProfileId: 'profile-order' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => {
        events.push('page');
        return page as any;
      },
      verifyOperationCapability: () => {
        events.push('verify');
      },
      assertGraphOperationOwnership: async () => {
        events.push('ownership');
      },
      consumeOperationCapability: async () => {
        events.push('consume');
      },
    });
    const response = await new Promise<any>((resolve, reject) => {
      handlers.executeGraphCallV5(
        unaryCall({
          session_id: 'session-order',
          vision_profile_id: 'profile-order',
          capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
          ad_account_id: '123',
          method: 'POST',
          endpoint: '/111',
          query_params: { status: 'PAUSED' },
          body_json: '',
          timeout_ms: 30_000,
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(response.status_code, 200);
    assert.deepEqual(events, ['verify', 'page', 'ownership', 'consume', 'mutation']);
  });

  it('ownership rejection happens before durable consume and browser mutation', async () => {
    let consumed = 0;
    let mutations = 0;
    const page = {
      waitForFunction: async () => true,
      evaluate: async () => {
        mutations += 1;
        return { status_code: 200, response_json: '{"success":true}' };
      },
    };
    const session = { id: 'session-owner', visionProfileId: 'profile-owner' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => page as any,
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => {
        throw new Error('Browser operation ownership preflight rejected the Meta target');
      },
      consumeOperationCapability: async () => {
        consumed += 1;
      },
    });
    const error = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(
        unaryCall({
          session_id: 'session-owner',
          vision_profile_id: 'profile-owner',
          capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
          ad_account_id: '123',
          method: 'POST',
          endpoint: '/111',
          query_params: { status: 'PAUSED' },
          body_json: '',
          timeout_ms: 30_000,
        }),
        (value: unknown) => resolve(value),
      );
    });

    assert.equal(error.code, 7);
    assert.equal(consumed, 0);
    assert.equal(mutations, 0);
  });

  it('a hung cancelled page is poisoned and the next same-key call is not blocked', async () => {
    _resetPageLocks();
    let firstStarted!: () => void;
    const firstStartedPromise = new Promise<void>((resolve) => {
      firstStarted = resolve;
    });
    const hungPage = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          firstStarted();
          return new Promise(() => undefined);
        }
        return undefined;
      },
    };
    const replacementPage = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => (
        args && typeof args === 'object' && 'endpoint' in args
          ? { status_code: 200, response_json: '{"success":true}' }
          : undefined
      ),
    };
    const session = { id: 'session-poison', visionProfileId: 'profile-poison' };
    let currentPage: any = hungPage;
    let poisonCalls = 0;
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => currentPage,
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => undefined,
      consumeOperationCapability: async () => undefined,
      poisonRolePage: (_session, role, actId, page) => {
        assert.equal(role, 'control');
        assert.equal(actId, '123');
        assert.equal(page, hungPage);
        poisonCalls += 1;
        currentPage = replacementPage;
      },
    });
    const request = {
      session_id: 'session-poison',
      vision_profile_id: 'profile-poison',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/111',
      query_params: { status: 'PAUSED' },
      body_json: '',
      timeout_ms: 30_000,
    };
    const firstCall = unaryCall(request);
    const firstError = new Promise<any>((resolve) => {
      void handlers.executeGraphCallV5(firstCall, (error: unknown) => resolve(error));
    });
    await firstStartedPromise;
    firstCall.emit('cancelled');
    assert.equal((await firstError).code, 1);

    const secondResponse = await new Promise<any>((resolve, reject) => {
      handlers.executeGraphCallV5(
        unaryCall(request),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });
    assert.equal(secondResponse.status_code, 200);
    assert.equal(poisonCalls, 1);
  });
});

describe('MetaApiService exact-profile health', () => {
  it('probes the requested canonical Vision profile and reports its live identity', async () => {
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      // Случайная вкладка чужого кабинета: identity из неё браться не должна.
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
    };
    let requestedProfile = '';
    let preferredCalls = 0;
    const fakeManager = {
      getSessionForVisionProfile: (profileId: string) => {
        requestedProfile = profileId;
        return session;
      },
      getPreferredSession: () => {
        preferredCalls += 1;
        return { id: 'wrong-session', visionProfileId: 'wrong-profile' };
      },
    } as unknown as SessionManager;
    const requestedActs: string[] = [];
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: (_session, actId) => {
        requestedActs.push(actId);
        return {} as any;
      },
      checkMetaApiHealth: async (_page, options) => {
        assert.equal(options?.fullProbe, true);
        assert.ok(options?.signal);
        return {
          healthy: true,
          currentUrl: 'https://adsmanager.facebook.com/?act=123',
          tokenPresent: true,
          tokenLength: 200,
          detail: 'ok',
          probePerformed: true,
          probeOk: true,
          probeStatusCode: 200,
          probeDurationMs: 10,
          probeDetail: 'ok',
        };
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: true,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '2108857220005012',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(requestedProfile, 'profile-exact');
    assert.equal(preferredCalls, 0);
    assert.deepEqual(requestedActs, ['2108857220005012']);
    assert.equal(response.healthy, true);
    assert.equal(response.browser_contract_version, 5);
    assert.equal(response.session_id, 'session-exact');
    assert.equal(response.vision_profile_id, 'profile-exact');
  });

  it('cancellation aborts the health probe and emits no late response', async () => {
    let probeStartedResolve!: () => void;
    const probeStarted = new Promise<void>((resolve) => { probeStartedResolve = resolve; });
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=123' },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => ({} as any),
      checkMetaApiHealth: async (_page, options) => {
        probeStartedResolve();
        await new Promise<void>((resolve) => {
          options?.signal?.addEventListener('abort', () => resolve(), { once: true });
        });
        return {
          healthy: true,
          currentUrl: 'https://adsmanager.facebook.com/?act=123',
          tokenPresent: true,
          tokenLength: 200,
          detail: 'ok',
          probePerformed: true,
          probeOk: true,
          probeStatusCode: 200,
          probeDurationMs: 10,
          probeDetail: 'ok',
        };
      },
    });
    const call = unaryCall({
      session_id: '',
      full_probe: true,
      expected_vision_profile_id: 'profile-exact',
      ad_account_id: '123',
    });
    let callbacks = 0;

    const pending = handlers.checkMetaApiHealth(call, () => { callbacks += 1; });
    await probeStarted;
    call.emit('cancelled');
    await pending;

    assert.equal(callbacks, 0);
  });

  it('does not fall back to a preferred session when the exact profile is absent', async () => {
    let preferredCalls = 0;
    let pageCalls = 0;
    const fakeManager = {
      getSessionForVisionProfile: () => {
        throw new Error('exact profile absent');
      },
      getPreferredSession: () => {
        preferredCalls += 1;
        return { id: 'wrong-session', visionProfileId: 'wrong-profile' };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        pageCalls += 1;
        return {} as any;
      },
    });

    const response = await new Promise<any>((resolve) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: true,
          expected_vision_profile_id: 'profile-exact',
        }),
        (_error: unknown, value: unknown) => resolve(value),
      );
    });

    assert.equal(response.healthy, false);
    assert.equal(response.session_id, '');
    assert.equal(response.vision_profile_id, '');
    assert.equal(preferredCalls, 0);
    assert.equal(pageCalls, 0);
  });

  it('health probe without a cabinet reuses a live tab and creates nothing', async () => {
    let createdPages = 0;
    const adsPage = {
      isClosed: () => false,
      url: () => 'https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=2108857220005012',
    };
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [adsPage] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        createdPages += 1;
        return {} as any;
      },
      checkMetaApiHealth: async (page) => {
        assert.equal(page, adsPage as any);
        return {
          healthy: true,
          currentUrl: 'https://adsmanager.facebook.com/?act=2108857220005012',
          tokenPresent: true,
          tokenLength: 200,
          detail: 'ok',
          probePerformed: false,
          probeOk: false,
          probeStatusCode: 0,
          probeDurationMs: 0,
          probeDetail: 'not_performed',
        };
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: false,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(createdPages, 0);
    assert.equal(response.healthy, true);
  });

  it('health probe without a cabinet and without a tab answers no_ads_manager_page', async () => {
    let createdPages = 0;
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        createdPages += 1;
        return {} as any;
      },
      checkMetaApiHealth: async () => {
        throw new Error('проба не должна запускаться без страницы');
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: false,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(createdPages, 0);
    assert.equal(response.healthy, false);
    assert.equal(response.detail, 'no_ads_manager_page');
    assert.equal(response.probe_performed, false);
    assert.equal(response.browser_contract_version, 5);
    assert.equal(response.session_id, 'session-exact');
  });

  // Кривой кабинет — не повод угадать его из вкладки. Ошибка отдаётся штатным
  // ответом healthy=false (как и session_not_found), потому что health_watchdog
  // хочет видеть состояние канала, а не gRPC-исключение.
  it('health probe reports a malformed cabinet id instead of guessing', async () => {
    let createdPages = 0;
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        createdPages += 1;
        return {} as any;
      },
      checkMetaApiHealth: async () => {
        throw new Error('проба не должна запускаться при кривом кабинете');
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: false,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: 'act_123abc',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(createdPages, 0);
    assert.equal(response.healthy, false);
    assert.equal(response.probe_performed, false);
    assert.match(response.detail, /ad_account_id must be 1\.\.32 digits/);
    // Кабинет из вкладки в ответ не просочился.
    assert.equal(response.detail.includes('1855748448431929'), false);
  });
});

// #183: money-мутация выполняется как page.evaluate(fetch) в живом SPA. Навигация
// посреди evaluate даёт «Execution context was destroyed» — ту самую ошибку, что
// убила залив 19.08.2026. Она приходит ПОСЛЕ отправки, поэтому исход неотличим
// от потерянного ответа. Эпоха страницы превращает это в отказ ДО отправки.
describe('MetaApiService: навигация money-страницы до отправки', () => {
  function epochPage(onEvaluate?: () => void) {
    const listeners = new Map<string, ((arg?: unknown) => void)[]>();
    const page: any = {
      on: (event: string, fn: (arg?: unknown) => void) => {
        listeners.set(event, [...(listeners.get(event) ?? []), fn]);
      },
      isClosed: () => false,
      mainFrame: () => 'main-frame',
      waitForFunction: async () => true,
      evaluate: async (_fn: unknown, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          onEvaluate?.();
          return { status_code: 200, response_json: '{"success":true}' };
        }
        return undefined;
      },
    };
    page.navigate = () => {
      for (const fn of listeners.get('request') ?? []) {
        fn({ isNavigationRequest: () => true, frame: () => 'main-frame' });
      }
    };
    return page;
  }

  function moneyRequest(sessionId: string, profileId: string) {
    return {
      session_id: sessionId,
      vision_profile_id: profileId,
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/111',
      query_params: { status: 'PAUSED' },
      body_json: '',
      timeout_ms: 30_000,
    };
  }

  it('навигация во время чтения владения: грант не списан, мутации нет', async () => {
    _resetPageLocks();
    let consumed = 0;
    let mutations = 0;
    const page = epochPage(() => {
      mutations += 1;
    });
    const session = { id: 'session-epoch', visionProfileId: 'profile-epoch' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => page as any,
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => {
        // Ровно то, что произошло на проде: страница ушла на другой URL, пока
        // мы читали владение объектом.
        page.navigate();
      },
      consumeOperationCapability: async () => {
        consumed += 1;
      },
    });

    const error = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(
        unaryCall(moneyRequest('session-epoch', 'profile-epoch')),
        (value: unknown) => resolve(value),
      );
    });

    // FAILED_PRECONDITION: отказ ДО внешней границы, а не потерянный ответ.
    assert.equal(error.code, 9);
    assert.match(String(error.message), /page_epoch_changed/);
    assert.equal(consumed, 0, 'одноразовый грант не должен быть списан');
    assert.equal(mutations, 0, 'ни один fetch не должен уйти в Meta');
  });

  it('навигация после списания гранта останавливает отправку', async () => {
    _resetPageLocks();
    let consumed = 0;
    let mutations = 0;
    const page = epochPage(() => {
      mutations += 1;
    });
    const session = { id: 'session-epoch2', visionProfileId: 'profile-epoch2' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => page as any,
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => undefined,
      consumeOperationCapability: async () => {
        consumed += 1;
        page.navigate();
      },
    });

    const error = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(
        unaryCall(moneyRequest('session-epoch2', 'profile-epoch2')),
        (value: unknown) => resolve(value),
      );
    });

    assert.equal(error.code, 9);
    assert.equal(consumed, 1, 'грант уже был списан — это честный REJECTED');
    assert.equal(mutations, 0, 'но мутация в Meta уйти не должна');
  });

  it('навигировавшая страница выбрасывается, следующий вызов работает на новой', async () => {
    _resetPageLocks();
    const poisoned: unknown[] = [];
    const pages = [epochPage(), epochPage()];
    let handedOut = 0;
    const session = { id: 'session-heal', visionProfileId: 'profile-heal' };
    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => {
        const page = pages[Math.min(handedOut, pages.length - 1)];
        handedOut += 1;
        return page as any;
      },
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => {
        if (handedOut === 1) pages[0].navigate();
      },
      consumeOperationCapability: async () => undefined,
      poisonRolePage: (_session, _role, _actId, page) => {
        poisoned.push(page);
      },
    });

    const first = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(
        unaryCall(moneyRequest('session-heal', 'profile-heal')),
        (value: unknown) => resolve(value),
      );
    });
    assert.equal(first.code, 9);
    assert.deepEqual(poisoned, [pages[0]], 'лечение — выбросить вкладку, а не перезагрузить');

    // Канал снова работоспособен: вторая операция идёт на новой странице.
    const second = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(
        unaryCall(moneyRequest('session-heal', 'profile-heal')),
        (_error: unknown, value: unknown) => resolve(value ?? _error),
      );
    });
    assert.equal(second.status_code, 200);
    assert.equal(handedOut, 2);
  });
});
