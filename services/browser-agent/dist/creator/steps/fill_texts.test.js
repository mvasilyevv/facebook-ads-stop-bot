"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const fill_texts_js_1 = require("./fill_texts.js");
// Идемпотентность: true только когда все три поля совпали.
(0, node_test_1.describe)('FillTextsStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении всех полей', () => {
        const s = new fill_texts_js_1.FillTextsStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { primary: 'P', headline: 'H', description: 'D' } }, { primary: 'P', headline: 'H', description: 'D' }), true);
    });
    (0, node_test_1.it)('isSatisfied false при отличии headline', () => {
        const s = new fill_texts_js_1.FillTextsStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { primary: 'P', headline: 'X', description: '' } }, { primary: 'P', headline: 'H' }), false);
    });
});
//# sourceMappingURL=fill_texts.test.js.map