"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_pixel_event_js_1 = require("./set_pixel_event.js");
const index_js_1 = require("../enums/index.js");
// Шаг идемпотентен только когда совпадают и pixelId, и событие.
(0, node_test_1.describe)('SetPixelEventStep', () => {
    (0, node_test_1.it)('isSatisfied true при совпадении event и pixelId', () => {
        const s = new set_pixel_event_js_1.SetPixelEventStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { event: index_js_1.PixelEvent.PURCHASE, pixelId: '123' } }, { event: index_js_1.PixelEvent.PURCHASE, pixelId: '123' }), true);
    });
    (0, node_test_1.it)('isSatisfied false при отличии', () => {
        const s = new set_pixel_event_js_1.SetPixelEventStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { event: index_js_1.PixelEvent.LEAD, pixelId: '123' } }, { event: index_js_1.PixelEvent.PURCHASE, pixelId: '123' }), false);
    });
});
//# sourceMappingURL=set_pixel_event.test.js.map