"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const text_js_1 = require("./text.js");
(0, node_test_1.describe)('normalizeText', () => {
    (0, node_test_1.it)('нижний регистр + триминг + схлопывает пробелы', () => {
        node_assert_1.default.equal((0, text_js_1.normalizeText)('  Сайт   и звонки  '), 'сайт и звонки');
    });
    (0, node_test_1.it)('удаляет невидимые символы', () => {
        node_assert_1.default.equal((0, text_js_1.normalizeText)('Web​site'), 'website');
    });
    (0, node_test_1.it)('идемпотентен', () => {
        const a = (0, text_js_1.normalizeText)('Сайт');
        node_assert_1.default.equal((0, text_js_1.normalizeText)(a), a);
    });
});
//# sourceMappingURL=text.test.js.map