"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const select_from_dropdown_js_1 = require("./select-from-dropdown.js");
// Проверяем матчинг подписи к enum по нормализованным синонимам.
const labels = {
    WEBSITE: { ru: ['Сайт', 'Веб-сайт'], en: ['Website'] },
    APP: { ru: ['Приложение'], en: ['App'] },
};
(0, node_test_1.describe)('resolveLabelToEnum', () => {
    (0, node_test_1.it)('матчит ru синоним', () => node_assert_1.default.equal((0, select_from_dropdown_js_1.resolveLabelToEnum)('  сайт ', labels), 'WEBSITE'));
    (0, node_test_1.it)('матчит en label', () => node_assert_1.default.equal((0, select_from_dropdown_js_1.resolveLabelToEnum)('App', labels), 'APP'));
    (0, node_test_1.it)('возвращает null при отсутствии', () => node_assert_1.default.equal((0, select_from_dropdown_js_1.resolveLabelToEnum)('xxx', labels), null));
});
//# sourceMappingURL=select-from-dropdown.test.js.map