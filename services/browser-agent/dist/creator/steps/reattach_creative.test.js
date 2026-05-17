"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const reattach_creative_js_1 = require("./reattach_creative.js");
// Идемпотентность — кол-во прикрепленных совпадает с paths.
(0, node_test_1.describe)('ReattachCreativeStep', () => {
    (0, node_test_1.it)('isSatisfied при равенстве кол-ва превью и paths', () => {
        const s = new reattach_creative_js_1.ReattachCreativeStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 1 }, { adName: 'A', paths: ['a.jpg'] }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 0 }, { adName: 'A', paths: ['a.jpg'] }), false);
    });
});
//# sourceMappingURL=reattach_creative.test.js.map