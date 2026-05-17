"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const jsdom_1 = require("jsdom");
const fiber_js_1 = require("./fiber.js");
(0, node_test_1.before)(() => {
    const dom = new jsdom_1.JSDOM('<!doctype html><html><body></body></html>');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.Element = dom.window.Element;
});
(0, node_test_1.describe)('fiber', () => {
    (0, node_test_1.it)('возвращает null если у элемента нет fiber-ключа', () => {
        const div = document.createElement('div');
        node_assert_1.default.equal((0, fiber_js_1.getFiber)(div), null);
        node_assert_1.default.equal((0, fiber_js_1.getReactProps)(div), null);
    });
    (0, node_test_1.it)('читает __reactProps$* по динамическому ключу', () => {
        const div = document.createElement('div');
        div.__reactProps$abc = { foo: 'bar' };
        node_assert_1.default.deepEqual((0, fiber_js_1.getReactProps)(div), { foo: 'bar' });
    });
});
//# sourceMappingURL=fiber.test.js.map