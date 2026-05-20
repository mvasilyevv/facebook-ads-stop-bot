"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.startRecording = startRecording;
exports.stopRecording = stopRecording;
exports.getStatus = getStatus;
exports._resetRecorder = _resetRecorder;
// Recorder: capture-phase обработчики click/input/change, маршрутизирует события
// через listSteps().match(ev, dom). Каждый зарегистрированный Step может
// распознать своё событие и вернуть PlanStep. Input debounce 800ms чтобы
// не плодить кучу шагов при наборе текста.
const registry_js_1 = require("./registry.js");
const state = {
    active: false,
    planName: '',
    recordedSteps: [],
    inputDebounceMs: 800,
    inputTimer: null,
    pendingInput: null,
    handlers: null,
};
function getSelector(el) {
    if (!el)
        return '';
    const testid = el.getAttribute?.('data-testid');
    if (testid)
        return `[data-testid="${testid}"]`;
    const surface = el.getAttribute?.('data-surface');
    if (surface)
        return `[data-surface="${surface}"]`;
    const role = el.getAttribute?.('role');
    const aria = el.getAttribute?.('aria-label');
    if (role && aria)
        return `[role="${role}"][aria-label="${CSS.escape(aria)}"]`;
    if (aria)
        return `[aria-label="${CSS.escape(aria)}"]`;
    const tag = el.tagName?.toLowerCase() ?? '';
    const id = el.id ? `#${el.id}` : '';
    return `${tag}${id}`;
}
function toRecordedEvent(type, ev) {
    const target = ev.target;
    const value = (() => {
        if (target instanceof HTMLInputElement)
            return target.value;
        if (target instanceof HTMLTextAreaElement)
            return target.value;
        if (target instanceof HTMLSelectElement)
            return target.value;
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
function getDomState() {
    return {
        url: typeof location !== 'undefined' ? location.href : '',
        title: typeof document !== 'undefined' ? document.title : '',
    };
}
function dispatch(recordedEvent) {
    if (!state.active)
        return;
    const dom = getDomState();
    for (const step of (0, registry_js_1.listSteps)()) {
        if (typeof step.match !== 'function')
            continue;
        let matched = false;
        try {
            matched = step.match(recordedEvent, dom);
        }
        catch {
            continue;
        }
        if (matched) {
            const planStep = { step: step.name, input: {} };
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
function flushPendingInput() {
    if (state.pendingInput) {
        dispatch(toRecordedEvent('input', state.pendingInput.ev));
        state.pendingInput = null;
    }
    if (state.inputTimer) {
        clearTimeout(state.inputTimer);
        state.inputTimer = null;
    }
}
function onClick(ev) {
    // Перед click сбрасываем накопленный input — иначе порядок шагов поплывёт.
    flushPendingInput();
    dispatch(toRecordedEvent('click', ev));
}
function onInput(ev) {
    state.pendingInput = { target: ev.target, ev };
    if (state.inputTimer)
        clearTimeout(state.inputTimer);
    state.inputTimer = setTimeout(() => {
        flushPendingInput();
    }, state.inputDebounceMs);
}
function onChange(ev) {
    flushPendingInput();
    dispatch(toRecordedEvent('change', ev));
}
function startRecording(planName) {
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
function stopRecording() {
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
function getStatus() {
    return {
        active: state.active,
        planName: state.planName,
        recordedSteps: state.recordedSteps.length,
    };
}
// Тестовый хук: позволяет сбросить состояние между тестами.
function _resetRecorder() {
    if (state.inputTimer)
        clearTimeout(state.inputTimer);
    state.active = false;
    state.planName = '';
    state.recordedSteps = [];
    state.inputTimer = null;
    state.pendingInput = null;
    state.handlers = null;
}
//# sourceMappingURL=recorder.js.map