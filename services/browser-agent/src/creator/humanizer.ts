// Гуманизированные паузы и (далее в задачах 3-5) DOM-события.
export const IdleRange = {
  SHORT: [80, 250] as const,
  BETWEEN_STEPS: [600, 2500] as const,
  BETWEEN_SCENES: [3000, 8000] as const,
  TYPING: [40, 180] as const,
  TYPING_BURST_PAUSE: [200, 800] as const,
} as const;

// Параметры частоты «вспышек» паузы при наборе: модуль = TYPING_BURST_MIN + rand(0..TYPING_BURST_JITTER).
const TYPING_BURST_MIN = 3;
const TYPING_BURST_JITTER = 6;

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

async function jitterHover(el: Element): Promise<void> {
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
  await jitterHover(el);
  await humanIdle(IdleRange.SHORT);
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  dispatchPointer(el, 'pointerdown', x, y);
  await humanIdle([20, 90] as const);
  dispatchPointer(el, 'pointerup', x, y);
  el.dispatchEvent(
    new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      clientX: x,
      clientY: y,
      button: 0,
      detail: 1,
    }),
  );
}

function setNativeInputValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
): void {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  setter?.call(el, value);
}

// Посимвольный гуманизированный ввод текста через native value setter и
// эмуляцию key/input событий, чтобы React контролируемые поля обновились.
export async function humanType(
  el: HTMLInputElement | HTMLTextAreaElement,
  text: string,
): Promise<void> {
  el.focus();
  await humanIdle(IdleRange.SHORT);
  let current = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keypress', { key: ch, bubbles: true }));
    current += ch;
    setNativeInputValue(el, current);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
    await humanIdle(IdleRange.TYPING);
    if (i > 0 && i % (TYPING_BURST_MIN + Math.floor(Math.random() * TYPING_BURST_JITTER)) === 0) {
      await humanIdle(IdleRange.TYPING_BURST_PAUSE);
    }
  }
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.blur();
}

// Гуманизированный скролл: несколько wheel-событий с переменной скоростью.
export async function humanScroll(el: Element, deltaY: number): Promise<void> {
  const ticks = 6 + Math.floor(Math.random() * 6);
  const per = deltaY / ticks;
  for (let i = 0; i < ticks; i++) {
    el.dispatchEvent(
      new WheelEvent('wheel', {
        bubbles: true,
        deltaY: per * (0.7 + Math.random() * 0.6),
      }),
    );
    await humanIdle([30, 110] as const);
  }
}
