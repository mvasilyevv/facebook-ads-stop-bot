import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetScheduleStartStep } from './set_schedule_start.js';

// Идемпотентность по ISO-дате.
describe('SetScheduleStartStep', () => {
  it('isSatisfied при совпадении ISO даты', () => {
    const s = new SetScheduleStartStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: '2026-05-17T09:00' },
        { isoDate: '2026-05-17T09:00' },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: '2026-05-17T09:00' },
        { isoDate: '2026-05-18T09:00' },
      ),
      false,
    );
  });
});
