"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const toggle_utils_js_1 = require("./toggle-utils.js");
// Проверяем, что общий селектор не захватывает checkbox выбора строки.
(0, node_test_1.default)('TOGGLE_SELECTOR targets only delivery switch elements', () => {
    strict_1.default.equal(toggle_utils_js_1.TOGGLE_SELECTOR, '[role="switch"]');
});
// Проверяем, что helper не теряет toggle, если fallback уже вернул сам switch-элемент.
(0, node_test_1.default)('resolveToggleHandleFromCell returns cell itself when it is already a switch', async () => {
    const switchHandle = {
        getAttribute: async (name) => (name === 'role' ? 'switch' : null),
        $: async () => null,
    };
    strict_1.default.equal(await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(switchHandle), switchHandle);
});
// Проверяем, что helper умеет находить вложенный switch внутри ячейки таблицы.
(0, node_test_1.default)('resolveToggleHandleFromCell finds nested switch for regular table cell', async () => {
    const nestedSwitch = {
        getAttribute: async (_name) => null,
        $: async () => null,
    };
    const cell = {
        getAttribute: async (_name) => null,
        $: async (selector) => (selector.includes('[role="switch"]') ? nestedSwitch : null),
    };
    strict_1.default.equal(await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(cell), nestedSwitch);
});
// Проверяем, что helper не принимает checkbox выбора строки за toggle объявления.
(0, node_test_1.default)('resolveToggleHandleFromCell ignores selection checkbox without switch role', async () => {
    const checkboxHandle = {
        getAttribute: async (name) => (name === 'type' ? 'checkbox' : null),
        $: async () => null,
    };
    strict_1.default.equal(await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(checkboxHandle), null);
});
// Проверяем, что helper не принимает generic aria-checked без switch role.
(0, node_test_1.default)('resolveToggleHandleFromCell ignores generic aria-checked without switch role', async () => {
    const ariaHandle = {
        getAttribute: async (name) => (name === 'aria-checked' ? 'false' : null),
        $: async () => null,
    };
    strict_1.default.equal(await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(ariaHandle), null);
});
//# sourceMappingURL=toggle-utils.test.js.map