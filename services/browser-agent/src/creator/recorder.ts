// Recorder: capture-phase обработчики click/input/change, маршрутизирует события
// через listSteps().match(ev, dom). Каждый зарегистрированный Step может
// распознать своё событие и вернуть PlanStep. Input debounce 800ms чтобы
// не плодить кучу шагов при наборе текста.
import { listSteps } from './registry.js';
import type { DomState, PlanStep, RecordedEvent } from './types.js';

interface RecorderState {
  active: boolean;
  planName: string;
  recordedSteps: PlanStep[];
  inputDebounceMs: number;
  inputTimer: ReturnType<typeof setTimeout> | null;
  pendingInput: { target: EventTarget; ev: Event } | null;
  handlers: {
    click: (e: Event) => void;
    input: (e: Event) => void;
    change: (e: Event) => void;
  } | null;
}

const state: RecorderState = {
  active: false,
  planName: '',
  recordedSteps: [],
  inputDebounceMs: 800,
  inputTimer: null,
  pendingInput: null,
  handlers: null,
};

function getSelector(el: Element): string {
  if (!el) return '';
  const testid = el.getAttribute?.('data-testid');
  if (testid) return `[data-testid="${testid}"]`;
  const surface = el.getAttribute?.('data-surface');
  if (surface) return `[data-surface="${surface}"]`;
  const role = el.getAttribute?.('role');
  const aria = el.getAttribute?.('aria-label');
  if (role && aria) return `[role="${role}"][aria-label="${CSS.escape(aria)}"]`;
  if (aria) return `[aria-label="${CSS.escape(aria)}"]`;
  const tag = el.tagName?.toLowerCase() ?? '';
  const id = el.id ? `#${el.id}` : '';
  return `${tag}${id}`;
}

function toRecordedEvent(type: RecordedEvent['type'], ev: Event): RecordedEvent {
  const target = ev.target as Element | null;
  const value = (() => {
    if (target instanceof HTMLInputElement) return target.value;
    if (target instanceof HTMLTextAreaElement) return target.value;
    if (target instanceof HTMLSelectElement) return target.value;
    return null;
  })();
  const text = (target?.textContent || '').trim().slice(0, 200);
  return {
    type,
    selector: target ? getSelector(target) : '',
    text,
    value,
  };
}

function getDomState(): DomState {
  return {
    url: typeof location !== 'undefined' ? location.href : '',
    title: typeof document !== 'undefined' ? document.title : '',
  };
}

function dispatch(recordedEvent: RecordedEvent): void {
  if (!state.active) return;
  const dom = getDomState();
  for (const step of listSteps()) {
    if (typeof step.match !== 'function') continue;
    let matched = false;
    try {
      matched = step.match(recordedEvent, dom);
    } catch {
      continue;
    }
    if (matched) {
      const planStep: PlanStep = { step: step.name, input: {} };
      const last = state.recordedSteps[state.recordedSteps.length - 1];
      if (last && last.step === planStep.step) {
        // Дедуп: одинаковый шаг подряд — пропускаем (например, повторный click_next).
        return;
      }
      state.recordedSteps.push(planStep);
      return;
    }
  }
}

function flushPendingInput(): void {
  if (state.pendingInput) {
    dispatch(toRecordedEvent('input', state.pendingInput.ev));
    state.pendingInput = null;
  }
  if (state.inputTimer) {
    clearTimeout(state.inputTimer);
    state.inputTimer = null;
  }
}

function onClick(ev: Event): void {
  // Перед click сбрасываем накопленный input — иначе порядок шагов поплывёт.
  flushPendingInput();
  dispatch(toRecordedEvent('click', ev));
}

function onInput(ev: Event): void {
  state.pendingInput = { target: ev.target as EventTarget, ev };
  if (state.inputTimer) clearTimeout(state.inputTimer);
  state.inputTimer = setTimeout(() => {
    flushPendingInput();
  }, state.inputDebounceMs);
}

function onChange(ev: Event): void {
  flushPendingInput();
  dispatch(toRecordedEvent('change', ev));
}

export function startRecording(planName: string): void {
  if (state.active) {
    throw new Error('recorder уже запущен');
  }
  state.active = true;
  state.planName = planName;
  state.recordedSteps = [];
  state.handlers = {
    click: onClick,
    input: onInput,
    change: onChange,
  };
  if (typeof document !== 'undefined') {
    document.addEventListener('click', state.handlers.click, true);
    document.addEventListener('input', state.handlers.input, true);
    document.addEventListener('change', state.handlers.change, true);
  }
}

export function stopRecording(): { planName: string; steps: PlanStep[] } {
  if (!state.active) {
    throw new Error('recorder не запущен');
  }
  flushPendingInput();
  if (state.handlers && typeof document !== 'undefined') {
    document.removeEventListener('click', state.handlers.click, true);
    document.removeEventListener('input', state.handlers.input, true);
    document.removeEventListener('change', state.handlers.change, true);
  }
  state.active = false;
  state.handlers = null;
  return { planName: state.planName, steps: [...state.recordedSteps] };
}

export function getStatus(): { active: boolean; planName: string; recordedSteps: number } {
  return {
    active: state.active,
    planName: state.planName,
    recordedSteps: state.recordedSteps.length,
  };
}

// Тестовый хук: позволяет сбросить состояние между тестами.
export function _resetRecorder(): void {
  if (state.inputTimer) clearTimeout(state.inputTimer);
  state.active = false;
  state.planName = '';
  state.recordedSteps = [];
  state.inputTimer = null;
  state.pendingInput = null;
  state.handlers = null;
}
