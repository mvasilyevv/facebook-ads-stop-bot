import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { assertGraphOperationOwnership } from './ownership.js';

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
