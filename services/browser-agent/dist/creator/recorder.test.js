"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const jsdom_1 = require("jsdom");
const registry_js_1 = require("./registry.js");
const recorder_js_1 = require("./recorder.js");
// Setup jsdom: recorder вешает capture-phase listener на document,
// поэтому нужно реальное DOM-окружение, а не голый globalThis.
(0, node_test_1.before)(() => {
    const dom = new jsdom_1.JSDOM('<!doctype html><html><body></body></html>', {
        pretendToBeVisual: true,
        url: 'https://www.facebook.com/adsmanager/',
    });
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.location = dom.window.location;
    globalThis.Event = dom.window.Event;
    globalThis.MouseEvent = dom.window.MouseEvent;
    globalThis.KeyboardEvent = dom.window.KeyboardEvent;
    globalThis.Element = dom.window.Element;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.HTMLSelectElement = dom.window.HTMLSelectElement;
    globalThis.CSS = dom.window.CSS ?? { escape: (s) => s };
});
// Универсальный stub-step, который матчит по типу события и записывается под нужным именем.
function makeStep(name, type) {
    return {
        name,
        match: (ev) => ev.type === type,
        detect: () => ({ kind: 'unknown' }),
        isSatisfied: () => false,
        execute: async () => ({}),
    };
}
(0, node_test_1.beforeEach)(() => {
    (0, recorder_js_1._resetRecorder)();
    (0, registry_js_1.clearRegistry)();
    document.body.innerHTML = '';
});
(0, node_test_1.after)(() => {
    (0, recorder_js_1._resetRecorder)();
    (0, registry_js_1.clearRegistry)();
});
(0, node_test_1.describe)('recorder lifecycle', () => {
    (0, node_test_1.it)('startRecording переводит в active, stopRecording возвращает план', () => {
        (0, recorder_js_1.startRecording)('test-plan');
        const st = (0, recorder_js_1.getStatus)();
        node_assert_1.default.equal(st.active, true);
        node_assert_1.default.equal(st.planName, 'test-plan');
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.equal(result.planName, 'test-plan');
        node_assert_1.default.deepEqual(result.steps, []);
        node_assert_1.default.equal((0, recorder_js_1.getStatus)().active, false);
    });
    (0, node_test_1.it)('второй startRecording без stop падает', () => {
        (0, recorder_js_1.startRecording)('a');
        node_assert_1.default.throws(() => (0, recorder_js_1.startRecording)('b'), /уже запущен/);
        (0, recorder_js_1.stopRecording)();
    });
    (0, node_test_1.it)('stopRecording без start падает', () => {
        node_assert_1.default.throws(() => (0, recorder_js_1.stopRecording)(), /не запущен/);
    });
});
(0, node_test_1.describe)('recorder dispatch', () => {
    (0, node_test_1.it)('записывает шаг при click если step.match вернул true', () => {
        (0, registry_js_1.registerStep)(makeStep('click_next', 'click'));
        (0, recorder_js_1.startRecording)('plan');
        const btn = document.createElement('button');
        document.body.appendChild(btn);
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.equal(result.steps.length, 1);
        node_assert_1.default.equal(result.steps[0].step, 'click_next');
    });
    (0, node_test_1.it)('дедуплицирует повторные одинаковые шаги подряд', () => {
        (0, registry_js_1.registerStep)(makeStep('click_next', 'click'));
        (0, recorder_js_1.startRecording)('plan');
        const btn = document.createElement('button');
        document.body.appendChild(btn);
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.equal(result.steps.length, 1);
    });
    (0, node_test_1.it)('не записывает событие если ни один step не сматчился', () => {
        (0, registry_js_1.registerStep)(makeStep('only_input', 'input'));
        (0, recorder_js_1.startRecording)('plan');
        const btn = document.createElement('button');
        document.body.appendChild(btn);
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.equal(result.steps.length, 0);
    });
    (0, node_test_1.it)('flushPendingInput срабатывает перед click — порядок сохраняется', async () => {
        (0, registry_js_1.registerStep)(makeStep('fill_text', 'input'));
        (0, registry_js_1.registerStep)(makeStep('click_next', 'click'));
        (0, recorder_js_1.startRecording)('plan');
        const input = document.createElement('input');
        const btn = document.createElement('button');
        document.body.appendChild(input);
        document.body.appendChild(btn);
        input.value = 'abc';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        // Click до истечения debounce — input должен быть зафлашен раньше click.
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.deepEqual(result.steps.map((s) => s.step), ['fill_text', 'click_next']);
    });
    (0, node_test_1.it)('change-событие тоже флашит pending input', () => {
        (0, registry_js_1.registerStep)(makeStep('fill_text', 'input'));
        (0, registry_js_1.registerStep)(makeStep('pick_option', 'change'));
        (0, recorder_js_1.startRecording)('plan');
        const input = document.createElement('input');
        const select = document.createElement('select');
        document.body.appendChild(input);
        document.body.appendChild(select);
        input.value = 'x';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        const result = (0, recorder_js_1.stopRecording)();
        node_assert_1.default.deepEqual(result.steps.map((s) => s.step), ['fill_text', 'pick_option']);
    });
    (0, node_test_1.it)('после stopRecording события больше не обрабатываются', () => {
        (0, registry_js_1.registerStep)(makeStep('click_next', 'click'));
        (0, recorder_js_1.startRecording)('plan');
        (0, recorder_js_1.stopRecording)();
        const btn = document.createElement('button');
        document.body.appendChild(btn);
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        node_assert_1.default.equal((0, recorder_js_1.getStatus)().recordedSteps, 0);
    });
});
(0, node_test_1.describe)('recorder getStatus', () => {
    (0, node_test_1.it)('счётчик растёт по мере записи', () => {
        (0, registry_js_1.registerStep)(makeStep('click_next', 'click'));
        (0, registry_js_1.registerStep)(makeStep('pick_option', 'change'));
        (0, recorder_js_1.startRecording)('plan');
        const btn = document.createElement('button');
        const sel = document.createElement('select');
        document.body.appendChild(btn);
        document.body.appendChild(sel);
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        node_assert_1.default.equal((0, recorder_js_1.getStatus)().recordedSteps, 2);
        (0, recorder_js_1.stopRecording)();
    });
});
//# sourceMappingURL=recorder.test.js.map