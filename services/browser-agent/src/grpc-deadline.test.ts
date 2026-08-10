import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bindGrpcDeadlineAbort, remainingGrpcDeadlineMs } from './grpc-deadline.js';

test('absolute gRPC deadline aborts the browser operation', async () => {
  const controller = new AbortController();
  const call = { getDeadline: () => new Date(Date.now() + 15) };
  const dispose = bindGrpcDeadlineAbort(call, controller);

  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(controller.signal.aborted, true);
  assert.equal(controller.signal.reason, 'grpc_deadline_exceeded');
  dispose();
});

test('expired and unbounded deadlines are handled without an unsafe timer', () => {
  const expired = new AbortController();
  bindGrpcDeadlineAbort({ getDeadline: () => new Date(Date.now() - 1) }, expired);
  assert.equal(expired.signal.aborted, true);

  const unbounded = new AbortController();
  const dispose = bindGrpcDeadlineAbort({ getDeadline: () => Infinity }, unbounded);
  assert.equal(remainingGrpcDeadlineMs({ getDeadline: () => Infinity }), undefined);
  assert.equal(unbounded.signal.aborted, false);
  dispose();
});
