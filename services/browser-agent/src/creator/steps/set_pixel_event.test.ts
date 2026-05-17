import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetPixelEventStep } from './set_pixel_event.js';
import { PixelEvent } from '../enums/index.js';

// Шаг идемпотентен только когда совпадают и pixelId, и событие.
describe('SetPixelEventStep', () => {
  it('isSatisfied true при совпадении event и pixelId', () => {
    const s = new SetPixelEventStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { event: PixelEvent.PURCHASE, pixelId: '123' } },
        { event: PixelEvent.PURCHASE, pixelId: '123' },
      ),
      true,
    );
  });

  it('isSatisfied false при отличии', () => {
    const s = new SetPixelEventStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { event: PixelEvent.LEAD, pixelId: '123' } },
        { event: PixelEvent.PURCHASE, pixelId: '123' },
      ),
      false,
    );
  });
});
