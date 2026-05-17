// Шаг: загрузка креативов. Эмитит request_upload, Python хост вызывает setInputFiles.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';

const BLOCK = {
  testid: 'media-section',
  aria: ['Медиа', 'Media'],
  text: ['медиа', 'media'],
};

export class UploadCreativesStep extends BaseStep<{ paths: string[] }, void> {
  name = 'upload_creatives';

  detect(): StepState {
    const block = findBlock(BLOCK);
    const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
    return { kind: 'matched', current: thumbs.length };
  }

  isSatisfied(state: StepState, input: { paths: string[] }): boolean {
    return (state.current as number) === input.paths.length;
  }

  protected async run(
    _s: StepState,
    input: { paths: string[] },
    ctx: PlanContext,
  ): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Media не найден');
    const fileInput = block.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) throw new Error('input[type=file] не найден в блоке Media');
    const id = `upload-${Date.now()}`;
    fileInput.setAttribute('data-fb-upload-id', id);
    ctx.emit('request_upload', {
      id,
      paths: input.paths,
      selector: `input[data-fb-upload-id="${id}"]`,
    });
  }
}
