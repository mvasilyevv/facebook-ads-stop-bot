import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { uploadImage, uploadVideoSingle } from './upload.js';

function cancellableUploadPage(result: Record<string, unknown>) {
  let startedResolve!: () => void;
  const started = new Promise<void>((resolve) => { startedResolve = resolve; });
  let finish!: (value: Record<string, unknown>) => void;
  let abortEvaluations = 0;
  const page = {
    evaluate: async (_fn: any, args: any) => {
      if (args && typeof args === 'object' && 'operationId' in args) {
        startedResolve();
        return new Promise<Record<string, unknown>>((resolve) => { finish = resolve; });
      }
      if (typeof args === 'string') {
        abortEvaluations += 1;
        finish(result);
      }
      return undefined;
    },
  };
  return { page: page as any, started, abortEvaluations: () => abortEvaluations };
}

describe('media upload browser-side cancellation', () => {
  it('AbortSignal reaches the in-page image fetch controller', async () => {
    const abort = new AbortController();
    const { page, started, abortEvaluations } = cancellableUploadPage({
      ok: false,
      hash: '',
      url: '',
      error: 'cancelled',
    });
    const pending = uploadImage(page, {
      adAccountId: 'act_123',
      filename: 'image.jpg',
      contentType: 'image/jpeg',
      fileBytes: Buffer.from('image'),
    }, { signal: abort.signal, operationId: 'image-cancel-test' });

    await started;
    abort.abort('grpc_cancelled');
    const result = await pending;

    assert.ok(abortEvaluations() >= 1);
    assert.equal(result.ok, false);
  });

  it('AbortSignal reaches the in-page video fetch controller', async () => {
    const abort = new AbortController();
    const { page, started, abortEvaluations } = cancellableUploadPage({
      ok: false,
      videoId: '',
      error: 'cancelled',
    });
    const pending = uploadVideoSingle(page, {
      adAccountId: 'act_123',
      filename: 'video.mp4',
      fileBytes: Buffer.from('video'),
    }, { signal: abort.signal, operationId: 'video-cancel-test' });

    await started;
    abort.abort('grpc_cancelled');
    const result = await pending;

    assert.ok(abortEvaluations() >= 1);
    assert.equal(result.ok, false);
  });
});
