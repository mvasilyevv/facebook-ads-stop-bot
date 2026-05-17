"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const registry_js_1 = require("./registry.js");
const dummy = {
    name: 'dummy',
    detect: () => ({ kind: 'unknown' }),
    isSatisfied: () => false,
    execute: async () => ({}),
};
(0, node_test_1.describe)('registry', () => {
    (0, node_test_1.beforeEach)(() => (0, registry_js_1.clearRegistry)());
    (0, node_test_1.it)('регистрирует и возвращает шаг по имени', () => {
        (0, registry_js_1.registerStep)(dummy);
        node_assert_1.default.strictEqual((0, registry_js_1.getStep)('dummy'), dummy);
    });
    (0, node_test_1.it)('listSteps возвращает все', () => {
        (0, registry_js_1.registerStep)(dummy);
        node_assert_1.default.deepEqual((0, registry_js_1.listSteps)().map((s) => s.name), ['dummy']);
    });
    (0, node_test_1.it)('падает при попытке зарегистрировать дубликат', () => {
        (0, registry_js_1.registerStep)(dummy);
        node_assert_1.default.throws(() => (0, registry_js_1.registerStep)(dummy), /already registered/);
    });
});
//# sourceMappingURL=registry.test.js.map