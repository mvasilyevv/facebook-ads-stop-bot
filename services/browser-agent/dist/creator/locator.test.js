"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const jsdom_1 = require("jsdom");
const locator_js_1 = require("./locator.js");
(0, node_test_1.before)(() => {
    const dom = new jsdom_1.JSDOM('<!doctype html><html><body></body></html>');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.Element = dom.window.Element;
    globalThis.Node = dom.window.Node;
    globalThis.NodeFilter = dom.window.NodeFilter;
    globalThis.CSS = dom.window.CSS ?? { escape: (s) => s.replace(/"/g, '\\"') };
});
(0, node_test_1.beforeEach)(() => {
    document.body.innerHTML = '';
});
(0, node_test_1.describe)('locator', () => {
    (0, node_test_1.it)('findByTestId', () => {
        const el = document.createElement('div');
        el.setAttribute('data-testid', 'geo');
        document.body.appendChild(el);
        node_assert_1.default.strictEqual((0, locator_js_1.findByTestId)('geo'), el);
    });
    (0, node_test_1.it)('findByAriaLabel', () => {
        const el = document.createElement('button');
        el.setAttribute('aria-label', 'Сохранить черновик');
        document.body.appendChild(el);
        node_assert_1.default.strictEqual((0, locator_js_1.findByAriaLabel)(['Сохранить черновик', 'Save draft']), el);
    });
    (0, node_test_1.it)('findByNormalizedText матчит нормализованный label', () => {
        const el = document.createElement('label');
        el.textContent = '  Сайт   и звонки  ';
        document.body.appendChild(el);
        node_assert_1.default.strictEqual((0, locator_js_1.findByNormalizedText)(['сайт и звонки']), el);
    });
    (0, node_test_1.it)('findBlock пробует testid → aria → text fallback в указанном порядке', () => {
        const el = document.createElement('section');
        el.setAttribute('data-testid', 'budget');
        document.body.appendChild(el);
        const found = (0, locator_js_1.findBlock)({ testid: 'budget', aria: ['Бюджет'], text: ['Бюджет'] });
        node_assert_1.default.strictEqual(found, el);
    });
});
//# sourceMappingURL=locator.test.js.map