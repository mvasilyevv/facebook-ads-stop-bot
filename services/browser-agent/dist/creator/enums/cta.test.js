"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const cta_js_1 = require("./cta.js");
// Проверяем enum CallToAction и наличие ru/en синонимов.
(0, node_test_1.describe)('CallToAction', () => {
    (0, node_test_1.it)('у каждого enum есть ru и en синонимы', () => {
        for (const k of Object.values(cta_js_1.CallToAction)) {
            const labels = cta_js_1.ctaLabels[k];
            node_assert_1.default.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
        }
    });
});
//# sourceMappingURL=cta.test.js.map