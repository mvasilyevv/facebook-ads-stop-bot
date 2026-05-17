// Гуманизированные паузы и (далее в задачах 3-5) DOM-события.
export const IdleRange = {
  SHORT: [80, 250] as const,
  BETWEEN_STEPS: [600, 2500] as const,
  BETWEEN_SCENES: [3000, 8000] as const,
  TYPING: [40, 180] as const,
  TYPING_BURST_PAUSE: [200, 800] as const,
} as const;

export type IdleRangeKey = readonly [number, number];

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

export function humanIdle(range: IdleRangeKey): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, rand(range[0], range[1])));
}

function dispatchPointer(el: Element, type: string, x: number, y: number): void {
  const ev = new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    composed: true,
    clientX: x,
    clientY: y,
    pointerType: 'mouse',
    isPrimary: true,
  });
  el.dispatchEvent(ev);
}

async function bezierHover(el: Element): Promise<void> {
  const rect = el.getBoundingClientRect();
  const tx = rect.left + rect.width / 2;
  const ty = rect.top + rect.height / 2;
  dispatchPointer(el, 'pointerover', tx, ty);
  const steps = 6 + Math.floor(Math.random() * 6);
  for (let i = 1; i <= steps; i++) {
    dispatchPointer(el, 'pointermove', tx + Math.random() * 2 - 1, ty + Math.random() * 2 - 1);
    await humanIdle([8, 24] as const);
  }
}

// Гуманизированный клик: hover с jitter → pointerdown → пауза → pointerup → click.
export async function humanClick(el: Element): Promise<void> {
  await bezierHover(el);
  await humanIdle(IdleRange.SHORT);
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  dispatchPointer(el, 'pointerdown', x, y);
  await humanIdle([20, 90] as const);
  dispatchPointer(el, 'pointerup', x, y);
  el.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x, clientY: y }),
  );
}
