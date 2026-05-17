// Фабрики шагов tree-навигации: duplicate/rename для ad и adset
// идентичны по логике — отличаются только role и формулировками ошибок.
import { BaseStep } from '../base.js';
import type { StepState } from '../../types.js';
import { findByAriaLabel, findByNormalizedText } from '../../locator.js';
import {
  humanClick,
  humanDoubleClick,
  humanType,
  humanIdle,
  IdleRange,
} from '../../humanizer.js';
import { findTreeNodeByName, listTreeNodeNames } from './tree-nav.js';

export type TreeRole = 'ad' | 'adset';

export interface DuplicateInput {
  sourceName: string;
  newName: string;
}

export interface RenameInput {
  from: string;
  to: string;
}

function roleLabel(role: TreeRole): string {
  return role === 'ad' ? 'Объявление' : 'Ad set';
}

export function createDuplicateStep(
  name: string,
  role: TreeRole,
): new () => BaseStep<DuplicateInput, void> {
  return class extends BaseStep<DuplicateInput, void> {
    name = name;

    detect(): StepState {
      return { kind: 'matched', current: listTreeNodeNames(role) };
    }

    isSatisfied(state: StepState, input: DuplicateInput): boolean {
      const names = (state.current as string[]) || [];
      return names.includes(input.newName);
    }

    protected async run(_s: StepState, input: DuplicateInput): Promise<void> {
      const node = findTreeNodeByName(role, input.sourceName);
      if (!node) {
        throw new Error(`${roleLabel(role)} "${input.sourceName}" не найден в дереве`);
      }
      const menu =
        node.querySelector<HTMLElement>(
          'button[aria-haspopup="menu"], [data-testid="row-menu"]',
        ) ?? node;
      await humanClick(menu);
      await humanIdle(IdleRange.SHORT);
      const dup =
        findByAriaLabel(['Дублировать', 'Duplicate']) ??
        findByNormalizedText(['дублировать', 'duplicate']);
      if (!dup) throw new Error('Пункт меню «Дублировать» не найден');
      await humanClick(dup);
      await humanIdle(IdleRange.BETWEEN_STEPS);
      const nameInput = document.querySelector<HTMLInputElement>(
        'input[type="text"][name*="name"], [data-testid="duplicate-name"] input',
      );
      if (nameInput) {
        await humanClick(nameInput);
        nameInput.select();
        await humanIdle(IdleRange.SHORT);
        await humanType(nameInput, input.newName);
      }
      const confirm =
        findByAriaLabel(['Дублировать', 'Duplicate', 'Подтвердить', 'Confirm']) ??
        findByNormalizedText(['дублировать', 'duplicate', 'подтвердить', 'confirm']);
      if (confirm) {
        await humanClick(confirm);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
    }
  };
}

export function createRenameStep(
  name: string,
  role: TreeRole,
): new () => BaseStep<RenameInput, void> {
  return class extends BaseStep<RenameInput, void> {
    name = name;

    detect(): StepState {
      return { kind: 'matched', current: listTreeNodeNames(role) };
    }

    isSatisfied(state: StepState, input: RenameInput): boolean {
      const names = (state.current as string[]) || [];
      return names.includes(input.to) && !names.includes(input.from);
    }

    protected async run(_s: StepState, input: RenameInput): Promise<void> {
      const node = findTreeNodeByName(role, input.from);
      if (!node) throw new Error(`${roleLabel(role)} "${input.from}" не найден`);
      await humanClick(node);
      await humanIdle(IdleRange.SHORT);
      // Двойной клик для входа в режим переименования (через humanizer, без байпасов).
      await humanDoubleClick(node);
      await humanIdle(IdleRange.SHORT);
      const input2 =
        node.querySelector<HTMLInputElement>('input[type="text"]') ??
        document.querySelector<HTMLInputElement>('[data-testid="rename-input"] input');
      if (!input2) throw new Error('Поле переименования не найдено');
      input2.select();
      await humanType(input2, input.to);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };
}
