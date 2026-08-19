import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as grpc from '@grpc/grpc-js';

import type { GraphApiCallParams } from './client.js';
import { assertGraphOperationOwnership } from './ownership.js';
import { grpcCodeForError } from './service.js';

const PAGE = {} as any;

describe('authoritative Meta object ownership preflight', () => {
  it('accepts only an exact act_<account> endpoint without inferred fallback', async () => {
    let graphReads = 0;
    await assertGraphOperationOwnership(
      PAGE,
      {
        method: 'POST',
        endpoint: '/act_123/campaigns',
        queryParams: {},
      },
      '123',
      {
        executeGraph: async () => {
          graphReads += 1;
          throw new Error('unexpected read');
        },
      },
    );
    assert.equal(graphReads, 0);

    await assert.rejects(
      () => assertGraphOperationOwnership(
        PAGE,
        {
          method: 'POST',
          endpoint: '/act_999/campaigns',
          queryParams: {},
        },
        '123',
      ),
      /ownership preflight rejected/,
    );
    for (const endpoint of [
      '/act_123/../act_999/campaigns',
      '/act_123/%2e%2e/act_999/campaigns',
      '/act_123//campaigns',
      '/act_123/campaigns?status=ACTIVE',
    ]) {
      await assert.rejects(
        () => assertGraphOperationOwnership(
          PAGE,
          {
            method: 'POST',
            endpoint,
            queryParams: {},
          },
          '123',
        ),
        /ownership preflight rejected/,
      );
    }
  });

  it('reads account_id for a numeric object and rejects another cabinet', async () => {
    const reads: unknown[] = [];
    await assertGraphOperationOwnership(
      PAGE,
      {
        method: 'POST',
        endpoint: '/111',
        queryParams: { status: 'PAUSED' },
      },
      '123',
      {
        executeGraph: async (_page, params) => {
          reads.push(params);
          return {
            statusCode: 200,
            responseJson: '{"account_id":"123"}',
            durationMs: 1,
          };
        },
      },
    );
    assert.deepEqual(reads, [{
      method: 'GET',
      endpoint: '/111',
      queryParams: { fields: 'account_id' },
      timeoutMs: 10_000,
    }]);

    await assert.rejects(
      () => assertGraphOperationOwnership(
        PAGE,
        {
          method: 'POST',
          endpoint: '/111',
          queryParams: { status: 'PAUSED' },
        },
        '123',
        {
          executeGraph: async () => ({
            statusCode: 200,
            responseJson: '{"account_id":"999"}',
            durationMs: 1,
          }),
        },
      ),
      /ownership preflight rejected/,
    );
  });

  it('proves every numeric batch target in one read and rejects a mixed cabinet', async () => {
    const mutationBatch = JSON.stringify([
      { method: 'POST', relative_url: '111?status=PAUSED' },
      { method: 'POST', relative_url: '222?status=PAUSED' },
    ]);
    let preflightBatch = '';
    await assertGraphOperationOwnership(
      PAGE,
      {
        method: 'POST',
        endpoint: '/',
        queryParams: { batch: mutationBatch },
      },
      '123',
      {
        executeGraph: async (_page, params) => {
          preflightBatch = params.queryParams.batch;
          return {
            statusCode: 200,
            responseJson: JSON.stringify([
              { code: 200, body: '{"account_id":"123"}' },
              { code: 200, body: '{"account_id":"123"}' },
            ]),
            durationMs: 1,
          };
        },
      },
    );
    assert.deepEqual(JSON.parse(preflightBatch), [
      { method: 'GET', relative_url: '111?fields=account_id' },
      { method: 'GET', relative_url: '222?fields=account_id' },
    ]);

    await assert.rejects(
      () => assertGraphOperationOwnership(
        PAGE,
        {
          method: 'POST',
          endpoint: '/',
          queryParams: { batch: mutationBatch },
        },
        '123',
        {
          executeGraph: async () => ({
            statusCode: 200,
            responseJson: JSON.stringify([
              { code: 200, body: '{"account_id":"123"}' },
              { code: 200, body: '{"account_id":"999"}' },
            ]),
            durationMs: 1,
          }),
        },
      ),
      /ownership preflight rejected/,
    );
  });

  it('rejects unknown batch shapes before any target lookup', async () => {
    let graphReads = 0;
    await assert.rejects(
      () => assertGraphOperationOwnership(
        PAGE,
        {
          method: 'POST',
          endpoint: '/',
          queryParams: {
            batch: JSON.stringify([
              {
                method: 'POST',
                relative_url: '111?status=PAUSED',
                omit_response_on_success: true,
              },
            ]),
          },
        },
        '123',
        {
          executeGraph: async () => {
            graphReads += 1;
            throw new Error('unexpected read');
          },
        },
      ),
      /unknown Graph batch shape/,
    );
    assert.equal(graphReads, 0);
  });

  it('separates a dead page, an unreachable channel and a Meta refusal', async () => {
    // executeGraphCall никогда не бросает и пакует в statusCode=0 три разных мира
    // (-1 токен, -2 сеть/таймаут/отмена, -3 смерть контекста страницы). Preflight
    // обязан различать их между собой и от честного HTTP-отказа Meta, иначе
    // причина оборванного залива неустановима.
    const cases = [
      {
        reason: 'channel_unreachable',
        statusCode: 0,
        code: -2,
        type: 'NetworkError',
        message: 'Failed to fetch',
      },
      {
        reason: 'page_context_lost',
        statusCode: 0,
        code: -3,
        type: 'PageEvaluateError',
        message: 'Execution context was destroyed',
      },
      {
        reason: 'meta_refused',
        statusCode: 400,
        code: 100,
        type: 'OAuthException',
        message: 'Unsupported get request',
      },
    ];

    const seenReasons = new Set<string>();
    for (const testCase of cases) {
      const probes: GraphApiCallParams[] = [
        { method: 'POST', endpoint: '/111', queryParams: { status: 'PAUSED' } },
        {
          method: 'POST',
          endpoint: '/',
          queryParams: {
            batch: JSON.stringify([{ method: 'POST', relative_url: '111?status=PAUSED' }]),
          },
        },
      ];
      for (const params of probes) {
        let graphReads = 0;
        await assert.rejects(
          () => assertGraphOperationOwnership(PAGE, params, '123', {
            executeGraph: async () => {
              graphReads += 1;
              return {
                statusCode: testCase.statusCode,
                responseJson: JSON.stringify({
                  error: {
                    code: testCase.code,
                    type: testCase.type,
                    message: testCase.message,
                  },
                }),
                durationMs: 1,
                error: {
                  code: testCase.code,
                  subcode: 0,
                  type: testCase.type,
                  message: testCase.message,
                  fbtraceId: '',
                },
              };
            },
          }),
          (err: unknown) => {
            const text = String((err as Error).message);
            assert.match(text, /ownership preflight/);
            assert.match(text, new RegExp(`reason=${testCase.reason}(?![a-z_])`));
            assert.match(text, new RegExp(`status=${testCase.statusCode}(?!\\d)`));
            assert.match(text, new RegExp(`code=${testCase.code}(?!\\d)`));
            // Классификация отказа не меняется: preflight остаётся pre-dispatch
            // FAILED_PRECONDITION, а не PERMISSION_DENIED и не INTERNAL.
            assert.equal(grpcCodeForError(err), grpc.status.FAILED_PRECONDITION);
            seenReasons.add(testCase.reason);
            return true;
          },
        );
        // Ровно одно чтение владения: отказ не превращается в повторную отправку.
        assert.equal(graphReads, 1);
      }
    }
    assert.equal(seenReasons.size, 3);
  });

  it('rejects encoded or path-shaped batch targets before lookup', async () => {
    for (const relativeUrl of [
      '111/../999?status=PAUSED',
      '111%2f..%2f999?status=PAUSED',
      '111',
      '111?status=PAUSED#ignored',
    ]) {
      await assert.rejects(
        () => assertGraphOperationOwnership(
          PAGE,
          {
            method: 'POST',
            endpoint: '/',
            queryParams: {
              batch: JSON.stringify([
                { method: 'POST', relative_url: relativeUrl },
              ]),
            },
          },
          '123',
        ),
        /ownership preflight rejected/,
      );
    }
  });
});
