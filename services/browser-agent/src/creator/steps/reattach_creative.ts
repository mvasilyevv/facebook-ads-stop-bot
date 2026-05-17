// Шаг: переприкрепление креативов к существующему объявлению.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { findTreeNodeByName } from './_helpers/tree-nav.js';
import { humanClick, humanIdle, IdleRange } from '../humanizer.js';

const MEDIA_BLOCK = {
  testid: 'media-section',
  aria: ['Медиа', 'Media'],
  text: ['медиа', 'media'],
};

interface ReattachInput {
  adName: string;
  paths: string[];
}

export class ReattachCreativeStep extends BaseStep<ReattachInput, void> {
  name = 'reattach_creative';

  detect(): StepState {
    const block = findBlock(MEDIA_BLOCK);
    const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
    return { kind: 'matched', current: thumbs.length };
  }

  isSatisfied(state: StepState, input: ReattachInput): boolean {
    return (state.current as number) === input.paths.length;
  }

  protected async run(
    _s: StepState,
    input: ReattachInput,
    ctx: PlanContext,
  ): Promise<void> {
    // Переключаемся на объявление в дереве (если найдено).
    const node = findTreeNodeByName('ad', input.adName);
    if (node) {
      await humanClick(node);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
    const block = findBlock(MEDIA_BLOCK);
    if (!block) throw new Error('Блок Media не найден');
    const fileInput = block.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) throw new Error('input[type=file] не найден в блоке Media');
    const id = `reattach-${Date.now()}`;
    fileInput.setAttribute('data-fb-upload-id', id);
    ctx.emit('request_upload', {
      id,
      paths: input.paths,
      selector: `input[data-fb-upload-id="${id}"]`,
    });
  }
}
