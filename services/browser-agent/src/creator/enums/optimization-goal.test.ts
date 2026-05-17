import { describe, it } from 'node:test';
import assert from 'node:assert';
import { OptimizationGoal, optimizationGoalLabels } from './optimization-goal.js';

// Проверяем enum OptimizationGoal и наличие ru/en синонимов.
describe('OptimizationGoal', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(OptimizationGoal)) {
      const labels = optimizationGoalLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
