import { describe, it } from 'node:test';
import assert from 'node:assert';
import { Objective, objectiveLabels } from './objective.js';

// Проверяем enum Objective и наличие ru/en синонимов.
describe('Objective', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(Objective)) {
      const labels = objectiveLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
