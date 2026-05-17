import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { clearRegistry, listSteps } from '../registry.js';

// Перед регистрацией создаём JSDOM, чтобы шаги, ссылающиеся на document/window,
// импортировались без ошибок (некоторые модули вычисляют DOM-зависимые константы при загрузке).
before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).Element = dom.window.Element;
  (globalThis as any).Node = dom.window.Node;
  (globalThis as any).NodeFilter = dom.window.NodeFilter;
  (globalThis as any).HTMLElement = dom.window.HTMLElement;
  (globalThis as any).HTMLInputElement = dom.window.HTMLInputElement;
  (globalThis as any).HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
  (globalThis as any).CSS = dom.window.CSS ?? { escape: (s: string) => s.replace(/"/g, '\\"') };
});

beforeEach(() => {
  clearRegistry();
});

describe('steps/index', () => {
  it('регистрирует все 23 шага', async () => {
    await import('./index.js');
    assert.equal(listSteps().length, 23);
  });
});
