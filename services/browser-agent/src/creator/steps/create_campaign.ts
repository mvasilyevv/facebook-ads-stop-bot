// Шаг: создание кампании (запуск wizard, ввод имени, выбор objective).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock, findByAriaLabel, findByNormalizedText } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';
import { Objective, objectiveLabels } from '../enums/index.js';
import {
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';

const OBJECTIVE_SPEC: DropdownSpec<Objective> = {
  block: {
    testid: 'campaign-objective',
    aria: ['Цель кампании', 'Campaign objective'],
    text: ['цель кампании', 'campaign objective'],
  },
  labels: objectiveLabels,
};

const NAME_BLOCK = {
  testid: 'campaign-name',
  aria: ['Название кампании', 'Campaign name'],
};

interface CreateCampaignInput {
  name: string;
  objective: Objective;
}

function readName(): string | null {
  const block = findBlock(NAME_BLOCK);
  if (!block) return null;
  const input = block.querySelector<HTMLInputElement>('input[type="text"]');
  return input?.value || null;
}

export class CreateCampaignStep extends BaseStep<CreateCampaignInput, void> {
  name = 'create_campaign';

  detect(): StepState {
    const name = readName();
    return name
      ? { kind: 'matched', current: { name } }
      : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: CreateCampaignInput): boolean {
    const c = state.current as { name: string } | undefined;
    return !!c && c.name === input.name;
  }

  protected async run(
    _s: StepState,
    input: CreateCampaignInput,
  ): Promise<void> {
    // Если кнопка «Создать» доступна — нажимаем (иначе предполагаем что мы уже в wizard).
    const createBtn =
      findByAriaLabel(['Создать', 'Create']) ?? findByNormalizedText(['создать', 'create']);
    if (createBtn) {
      await humanClick(createBtn);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
    await selectValue(OBJECTIVE_SPEC, input.objective);
    const block = findBlock(NAME_BLOCK);
    if (block) {
      const field = block.querySelector<HTMLInputElement>('input[type="text"]');
      if (field) {
        await humanClick(field);
        field.select();
        await humanIdle(IdleRange.SHORT);
        await humanType(field, input.name);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
    }
  }
}
