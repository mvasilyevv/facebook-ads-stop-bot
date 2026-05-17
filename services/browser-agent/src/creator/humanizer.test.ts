import { describe, it } from 'node:test';
import assert from 'node:assert';
import { humanIdle, IdleRange } from './humanizer.js';

describe('humanIdle', () => {
  it('ждёт в пределах диапазона', async () => {
    const start = Date.now();
    await humanIdle(IdleRange.SHORT);
    const elapsed = Date.now() - start;
    assert.ok(elapsed >= 50 && elapsed <= 600, `elapsed=${elapsed}`);
  });
});
