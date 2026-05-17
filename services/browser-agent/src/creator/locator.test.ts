import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import {
  findByTestId,
  findByAriaLabel,
  findByNormalizedText,
  findBlock,
} from './locator.js';

before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).Element = dom.window.Element;
  (globalThis as any).Node = dom.window.Node;
  (globalThis as any).NodeFilter = dom.window.NodeFilter;
  (globalThis as any).CSS = dom.window.CSS ?? { escape: (s: string) => s.replace(/"/g, '\\"') };
});

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('locator', () => {
  it('findByTestId', () => {
    const el = document.createElement('div');
    el.setAttribute('data-testid', 'geo');
    document.body.appendChild(el);
    assert.strictEqual(findByTestId('geo'), el);
  });

  it('findByAriaLabel', () => {
    const el = document.createElement('button');
    el.setAttribute('aria-label', 'Сохранить черновик');
    document.body.appendChild(el);
    assert.strictEqual(findByAriaLabel(['Сохранить черновик', 'Save draft']), el);
  });

  it('findByNormalizedText матчит нормализованный label', () => {
    const el = document.createElement('label');
    el.textContent = '  Сайт   и звонки  ';
    document.body.appendChild(el);
    assert.strictEqual(findByNormalizedText(['сайт и звонки']), el);
  });

  it('findBlock пробует testid → aria → text fallback в указанном порядке', () => {
    const el = document.createElement('section');
    el.setAttribute('data-testid', 'budget');
    document.body.appendChild(el);
    const found = findBlock({ testid: 'budget', aria: ['Бюджет'], text: ['Бюджет'] });
    assert.strictEqual(found, el);
  });
});
