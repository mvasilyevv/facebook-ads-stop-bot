// Шаг: выбор пикселя по ID (поиск + клик) и события Pixel (Purchase/Lead/...).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { PixelEvent, pixelEventLabels } from '../enums/index.js';
import {
  readSelectedValue,
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const EVENT_SPEC: DropdownSpec<PixelEvent> = {
  block: {
    testid: 'pixel-event',
    aria: ['Событие конверсии', 'Conversion event'],
    text: ['событие конверсии', 'conversion event'],
  },
  labels: pixelEventLabels,
};

const PIXEL_BLOCK = {
  testid: 'pixel-selector',
  aria: ['Источник данных', 'Data source', 'Пиксель'],
  text: ['пиксель', 'data source'],
};

interface PixelEventInput {
  pixelId: string;
  event: PixelEvent;
}

function readCurrentPixelId(): string | null {
  const block = findBlock(PIXEL_BLOCK);
  if (!block) return null;
  const id = block.querySelector('[data-pixel-id]')?.getAttribute('data-pixel-id');
  return id ?? null;
}

export class SetPixelEventStep extends BaseStep<PixelEventInput, void> {
  name = 'set_pixel_event';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const event = readSelectedValue(EVENT_SPEC);
    const pixelId = readCurrentPixelId();
    if (event && pixelId) {
      return { kind: 'matched', current: { event, pixelId } };
    }
    return { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: PixelEventInput): boolean {
    if (state.kind !== 'matched') return false;
    const cur = state.current as PixelEventInput;
    return cur.event === input.event && cur.pixelId === input.pixelId;
  }

  protected async run(_state: StepState, input: PixelEventInput): Promise<void> {
    // Выбор пикселя по ID через поле поиска источника данных.
    const pxBlock = findBlock(PIXEL_BLOCK);
    if (pxBlock) {
      const trigger = pxBlock.querySelector<HTMLElement>(
        'button[aria-haspopup="listbox"], [role="combobox"]',
      );
      if (trigger) {
        await humanClick(trigger);
        await humanIdle(IdleRange.SHORT);
        const search = document.querySelector<HTMLInputElement>(
          'input[role="combobox"], input[type="search"]',
        );
        if (search) {
          await humanType(search, input.pixelId);
          await humanIdle(IdleRange.BETWEEN_STEPS);
          const option =
            document.querySelector<HTMLElement>(
              `[role="option"][data-pixel-id="${input.pixelId}"]`,
            ) ?? document.querySelector<HTMLElement>('[role="option"]');
          if (!option) throw new Error(`Пиксель ${input.pixelId} не найден в списке`);
          await humanClick(option);
          await humanIdle(IdleRange.BETWEEN_STEPS);
        }
      }
    }
    await selectValue(EVENT_SPEC, input.event);
  }
}
