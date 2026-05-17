"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_cta_js_1 = require("./set_cta.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность CTA.
(0, node_test_1.describe)('SetCtaStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении CTA', () => {
        const s = new set_cta_js_1.SetCtaStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.CallToAction.SHOP_NOW }, { value: index_js_1.CallToAction.SHOP_NOW }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.CallToAction.LEARN_MORE }, { value: index_js_1.CallToAction.SHOP_NOW }), false);
    });
});
//# sourceMappingURL=set_cta.test.js.map