"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const jsdom_1 = require("jsdom");
const humanizer_js_1 = require("./humanizer.js");
(0, node_test_1.before)(() => {
    const dom = new jsdom_1.JSDOM('<!doctype html><html><body></body></html>', {
        pretendToBeVisual: true,
    });
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.PointerEvent = dom.window.PointerEvent ?? dom.window.MouseEvent;
    globalThis.MouseEvent = dom.window.MouseEvent;
    globalThis.KeyboardEvent = dom.window.KeyboardEvent;
    globalThis.Event = dom.window.Event;
    globalThis.WheelEvent = dom.window.WheelEvent;
    globalThis.Node = dom.window.Node;
    globalThis.Element = dom.window.Element;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
});
(0, node_test_1.describe)('humanIdle', () => {
    (0, node_test_1.it)('ждёт в пределах диапазона', async () => {
        const start = Date.now();
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        const elapsed = Date.now() - start;
        node_assert_1.default.ok(elapsed >= 50 && elapsed <= 600, `elapsed=${elapsed}`);
    });
});
(0, node_test_1.describe)('humanClick', () => {
    (0, node_test_1.it)('диспатчит pointerdown→pointerup→click на элемент', async () => {
        const div = document.createElement('div');
        document.body.appendChild(div);
        const events = [];
        ['pointerover', 'pointermove', 'pointerdown', 'pointerup', 'click'].forEach((t) => div.addEventListener(t, () => events.push(t)));
        await (0, humanizer_js_1.humanClick)(div);
        node_assert_1.default.deepEqual(events.slice(-3), ['pointerdown', 'pointerup', 'click']);
    });
});
(0, node_test_1.describe)('humanType', () => {
    (0, node_test_1.it)('вводит текст символ за символом и диспатчит input/keydown', async () => {
        const input = document.createElement('input');
        document.body.appendChild(input);
        const events = [];
        ['keydown', 'keypress', 'input', 'keyup'].forEach((t) => input.addEventListener(t, () => events.push(t)));
        await (0, humanizer_js_1.humanType)(input, 'ab');
        node_assert_1.default.equal(input.value, 'ab');
        node_assert_1.default.ok(events.includes('input'));
        node_assert_1.default.ok(events.includes('keydown'));
    });
});
(0, node_test_1.describe)('humanScroll', () => {
    (0, node_test_1.it)('диспатчит wheel-события', async () => {
        const div = document.createElement('div');
        document.body.appendChild(div);
        let count = 0;
        div.addEventListener('wheel', () => count++);
        await (0, humanizer_js_1.humanScroll)(div, 300);
        node_assert_1.default.ok(count >= 3, `wheel events=${count}`);
    });
});
//# sourceMappingURL=humanizer.test.js.map