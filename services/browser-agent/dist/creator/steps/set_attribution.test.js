"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_attribution_js_1 = require("./set_attribution.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность по выбранному окну атрибуции.
(0, node_test_1.describe)('SetAttributionStep', () => {
    (0, node_test_1.it)('isSatisfied по совпадению окна атрибуции', () => {
        const s = new set_attribution_js_1.SetAttributionStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.AttributionWindow.CLICK_7D }, { value: index_js_1.AttributionWindow.CLICK_7D }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.AttributionWindow.CLICK_1D }, { value: index_js_1.AttributionWindow.CLICK_7D }), false);
    });
});
//# sourceMappingURL=set_attribution.test.js.map