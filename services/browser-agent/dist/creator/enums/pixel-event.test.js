"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const pixel_event_js_1 = require("./pixel-event.js");
// Проверяем enum PixelEvent и наличие ru/en синонимов.
(0, node_test_1.describe)('PixelEvent', () => {
    (0, node_test_1.it)('у каждого enum есть ru и en синонимы', () => {
        for (const k of Object.values(pixel_event_js_1.PixelEvent)) {
            const labels = pixel_event_js_1.pixelEventLabels[k];
            node_assert_1.default.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
        }
    });
});
//# sourceMappingURL=pixel-event.test.js.map