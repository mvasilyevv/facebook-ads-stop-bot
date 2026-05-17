// Хелпер выбора значения из выпадающего списка по LabelMap (ru+en синонимы).
// Используется enum-шагами через DropdownSpec.
import { humanClick, humanIdle, IdleRange } from '../../humanizer.js';
import { findBlock, findByNormalizedText } from '../../locator.js';
import { normalizeText } from '../../text.js';
import type { BlockLookup } from '../../locator.js';
import type { LabelMap } from '../../enums/index.js';

export type { LabelMap };

export function resolveLabelToEnum<T extends string>(
  label: string,
  labels: LabelMap<T>,
): T | null {
  const norm = normalizeText(label);
  for (const [enumKey, syns] of Object.entries(labels) as [
    T,
    { ru: string[]; en: string[] },
  ][]) {
    const all = [...syns.ru, ...syns.en].map(normalizeText);
    if (all.includes(norm)) return enumKey;
  }
  return null;
}

export interface DropdownSpec<T extends string> {
  block: BlockLookup;
  labels: LabelMap<T>;
}

// Читает текущее выбранное значение в дропдауне и резолвит его в enum.
export function readSelectedValue<T extends string>(
  spec: DropdownSpec<T>,
): T | null {
  const block = findBlock(spec.block);
  if (!block) return null;
  const visible = block.querySelector(
    '[aria-selected="true"], [data-selected="true"], button[aria-haspopup="listbox"]',
  );
  const text = (visible?.textContent ?? '').trim();
  if (!text) return null;
  return resolveLabelToEnum(text, spec.labels);
}

// Открывает дропдаун и выбирает опцию, соответствующую enum target.
export async function selectValue<T extends string>(
  spec: DropdownSpec<T>,
  target: T,
): Promise<void> {
  const block = findBlock(spec.block);
  if (!block) throw new Error(`Блок не найден: ${JSON.stringify(spec.block)}`);
  const trigger = block.querySelector<HTMLElement>(
    'button[aria-haspopup="listbox"], [role="combobox"]',
  );
  if (!trigger) throw new Error('Trigger дропдауна не найден');
  await humanClick(trigger);
  await humanIdle(IdleRange.SHORT);
  const syns = spec.labels[target];
  if (!syns) throw new Error(`Unknown enum value: ${target}`);
  const option = findByNormalizedText([...syns.ru, ...syns.en]);
  if (!option) {
    throw new Error(
      `Опция "${target}" не найдена в дропдауне (синонимы: ${[...syns.ru, ...syns.en].join(', ')})`,
    );
  }
  await humanClick(option);
  await humanIdle(IdleRange.BETWEEN_STEPS);
}
