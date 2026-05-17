"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const attribution_js_1 = require("./attribution.js");
// Проверяем enum AttributionWindow и наличие ru/en синонимов.
(0, node_test_1.describe)('AttributionWindow', () => {
    (0, node_test_1.it)('у каждого enum есть ru и en синонимы', () => {
        for (const k of Object.values(attribution_js_1.AttributionWindow)) {
            const labels = attribution_js_1.attributionLabels[k];
            node_assert_1.default.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
        }
    });
});
//# sourceMappingURL=attribution.test.js.map