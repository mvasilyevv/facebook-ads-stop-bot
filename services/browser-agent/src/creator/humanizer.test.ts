import { describe, it, before } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { humanIdle, IdleRange, humanClick } from './humanizer.js';

before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    pretendToBeVisual: true,
  });
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).PointerEvent = dom.window.PointerEvent ?? dom.window.MouseEvent;
  (globalThis as any).MouseEvent = dom.window.MouseEvent;
  (globalThis as any).KeyboardEvent = dom.window.KeyboardEvent;
  (globalThis as any).Event = dom.window.Event;
  (globalThis as any).WheelEvent = dom.window.WheelEvent;
  (globalThis as any).Node = dom.window.Node;
  (globalThis as any).Element = dom.window.Element;
  (globalThis as any).HTMLElement = dom.window.HTMLElement;
  (globalThis as any).HTMLInputElement = dom.window.HTMLInputElement;
  (globalThis as any).HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
});

describe('humanIdle', () => {
  it('ждёт в пределах диапазона', async () => {
    const start = Date.now();
    await humanIdle(IdleRange.SHORT);
    const elapsed = Date.now() - start;
    assert.ok(elapsed >= 50 && elapsed <= 600, `elapsed=${elapsed}`);
  });
});

describe('humanClick', () => {
  it('диспатчит pointerdown→pointerup→click на элемент', async () => {
    const div = document.createElement('div');
    document.body.appendChild(div);
    const events: string[] = [];
    ['pointerover', 'pointermove', 'pointerdown', 'pointerup', 'click'].forEach((t) =>
      div.addEventListener(t, () => events.push(t)),
    );
    await humanClick(div);
    assert.deepEqual(events.slice(-3), ['pointerdown', 'pointerup', 'click']);
  });
});
