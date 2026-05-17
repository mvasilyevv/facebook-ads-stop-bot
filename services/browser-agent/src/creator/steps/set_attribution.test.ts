import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetAttributionStep } from './set_attribution.js';
import { AttributionWindow } from '../enums/index.js';

// Идемпотентность по выбранному окну атрибуции.
describe('SetAttributionStep', () => {
  it('isSatisfied по совпадению окна атрибуции', () => {
    const s = new SetAttributionStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: AttributionWindow.CLICK_7D },
        { value: AttributionWindow.CLICK_7D },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: AttributionWindow.CLICK_1D },
        { value: AttributionWindow.CLICK_7D },
      ),
      false,
    );
  });
});
