import { describe, it, before, beforeEach, after } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { clearRegistry, registerStep } from './registry.js';
import {
  startRecording,
  stopRecording,
  getStatus,
  _resetRecorder,
} from './recorder.js';
import type { Step } from './types.js';

// Setup jsdom: recorder вешает capture-phase listener на document,
// поэтому нужно реальное DOM-окружение, а не голый globalThis.
before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    pretendToBeVisual: true,
    url: 'https://www.facebook.com/adsmanager/',
  });
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).location = dom.window.location;
  (globalThis as any).Event = dom.window.Event;
  (globalThis as any).MouseEvent = dom.window.MouseEvent;
  (globalThis as any).KeyboardEvent = dom.window.KeyboardEvent;
  (globalThis as any).Element = dom.window.Element;
  (globalThis as any).HTMLElement = dom.window.HTMLElement;
  (globalThis as any).HTMLInputElement = dom.window.HTMLInputElement;
  (globalThis as any).HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
  (globalThis as any).HTMLSelectElement = dom.window.HTMLSelectElement;
  (globalThis as any).CSS = (dom.window as any).CSS ?? { escape: (s: string) => s };
});

// Универсальный stub-step, который матчит по типу события и записывается под нужным именем.
function makeStep(name: string, type: 'click' | 'input' | 'change'): Step {
  return {
    name,
    match: (ev) => ev.type === type,
    detect: () => ({ kind: 'unknown' }),
    isSatisfied: () => false,
    execute: async () => ({}),
  };
}

beforeEach(() => {
  _resetRecorder();
  clearRegistry();
  document.body.innerHTML = '';
});

after(() => {
  _resetRecorder();
  clearRegistry();
});

describe('recorder lifecycle', () => {
  it('startRecording переводит в active, stopRecording возвращает план', () => {
    startRecording('test-plan');
    const st = getStatus();
    assert.equal(st.active, true);
    assert.equal(st.planName, 'test-plan');
    const result = stopRecording();
    assert.equal(result.planName, 'test-plan');
    assert.deepEqual(result.steps, []);
    assert.equal(getStatus().active, false);
  });

  it('второй startRecording без stop падает', () => {
    startRecording('a');
    assert.throws(() => startRecording('b'), /уже запущен/);
    stopRecording();
  });

  it('stopRecording без start падает', () => {
    assert.throws(() => stopRecording(), /не запущен/);
  });
});

describe('recorder dispatch', () => {
  it('записывает шаг при click если step.match вернул true', () => {
    registerStep(makeStep('click_next', 'click'));
    startRecording('plan');
    const btn = document.createElement('button');
    document.body.appendChild(btn);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const result = stopRecording();
    assert.equal(result.steps.length, 1);
    assert.equal(result.steps[0]!.step, 'click_next');
  });

  it('дедуплицирует повторные одинаковые шаги подряд', () => {
    registerStep(makeStep('click_next', 'click'));
    startRecording('plan');
    const btn = document.createElement('button');
    document.body.appendChild(btn);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const result = stopRecording();
    assert.equal(result.steps.length, 1);
  });

  it('не записывает событие если ни один step не сматчился', () => {
    registerStep(makeStep('only_input', 'input'));
    startRecording('plan');
    const btn = document.createElement('button');
    document.body.appendChild(btn);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const result = stopRecording();
    assert.equal(result.steps.length, 0);
  });

  it('flushPendingInput срабатывает перед click — порядок сохраняется', async () => {
    registerStep(makeStep('fill_text', 'input'));
    registerStep(makeStep('click_next', 'click'));
    startRecording('plan');
    const input = document.createElement('input');
    const btn = document.createElement('button');
    document.body.appendChild(input);
    document.body.appendChild(btn);
    input.value = 'abc';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    // Click до истечения debounce — input должен быть зафлашен раньше click.
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const result = stopRecording();
    assert.deepEqual(
      result.steps.map((s) => s.step),
      ['fill_text', 'click_next'],
    );
  });

  it('change-событие тоже флашит pending input', () => {
    registerStep(makeStep('fill_text', 'input'));
    registerStep(makeStep('pick_option', 'change'));
    startRecording('plan');
    const input = document.createElement('input');
    const select = document.createElement('select');
    document.body.appendChild(input);
    document.body.appendChild(select);
    input.value = 'x';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const result = stopRecording();
    assert.deepEqual(
      result.steps.map((s) => s.step),
      ['fill_text', 'pick_option'],
    );
  });

  it('после stopRecording события больше не обрабатываются', () => {
    registerStep(makeStep('click_next', 'click'));
    startRecording('plan');
    stopRecording();
    const btn = document.createElement('button');
    document.body.appendChild(btn);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    assert.equal(getStatus().recordedSteps, 0);
  });
});

describe('recorder getStatus', () => {
  it('счётчик растёт по мере записи', () => {
    registerStep(makeStep('click_next', 'click'));
    registerStep(makeStep('pick_option', 'change'));
    startRecording('plan');
    const btn = document.createElement('button');
    const sel = document.createElement('select');
    document.body.appendChild(btn);
    document.body.appendChild(sel);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    assert.equal(getStatus().recordedSteps, 2);
    stopRecording();
  });
});
