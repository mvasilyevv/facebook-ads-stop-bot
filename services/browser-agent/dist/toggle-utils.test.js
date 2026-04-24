"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const toggle_utils_js_1 = require("./toggle-utils.js");
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
        $: async (selector) => (selector === '[role="switch"]' ? nestedSwitch : null),
    };
    strict_1.default.equal(await (0, toggle_utils_js_1.resolveToggleHandleFromCell)(cell), nestedSwitch);
});
//# sourceMappingURL=toggle-utils.test.js.map