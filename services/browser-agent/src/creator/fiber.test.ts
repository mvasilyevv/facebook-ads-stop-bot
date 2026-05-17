import { describe, it, before } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { getFiber, getReactProps } from './fiber.js';

before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).Element = dom.window.Element;
});

describe('fiber', () => {
  it('возвращает null если у элемента нет fiber-ключа', () => {
    const div = document.createElement('div');
    assert.equal(getFiber(div), null);
    assert.equal(getReactProps(div), null);
  });

  it('читает __reactProps$* по динамическому ключу', () => {
    const div: any = document.createElement('div');
    div.__reactProps$abc = { foo: 'bar' };
    assert.deepEqual(getReactProps(div), { foo: 'bar' });
  });
});
