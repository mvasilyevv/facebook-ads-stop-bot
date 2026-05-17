import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetBudgetStep } from './set_budget.js';
import { Currency } from '../enums/index.js';

// Идемпотентность бюджета (по сумме).
describe('SetBudgetStep', () => {
  it('isSatisfied при равной сумме', () => {
    const s = new SetBudgetStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { amount: 50, currency: 'USD' } },
        { amount: 50, currency: Currency.USD },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { amount: 50, currency: 'USD' } },
        { amount: 100, currency: Currency.USD },
      ),
      false,
    );
  });
});
