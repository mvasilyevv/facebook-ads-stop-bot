import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetOptimizationGoalStep } from './set_optimization_goal.js';
import { OptimizationGoal } from '../enums/index.js';

// Идемпотентность по выбранной цели оптимизации.
describe('SetOptimizationGoalStep', () => {
  it('isSatisfied по совпадению значения', () => {
    const s = new SetOptimizationGoalStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: OptimizationGoal.CONVERSIONS },
        { value: OptimizationGoal.CONVERSIONS },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: OptimizationGoal.LINK_CLICKS },
        { value: OptimizationGoal.CONVERSIONS },
      ),
      false,
    );
  });
});
