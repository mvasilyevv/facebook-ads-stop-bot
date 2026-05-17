"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_tracking_url_js_1 = require("./set_tracking_url.js");
// Идемпотентность по URL.
(0, node_test_1.describe)('SetTrackingUrlStep', () => {
    (0, node_test_1.it)('isSatisfied при равных URL', () => {
        const s = new set_tracking_url_js_1.SetTrackingUrlStep();
        const url = 'https://t.co?p={{adset.id}}';
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: url }, { url }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 'x' }, { url }), false);
    });
});
//# sourceMappingURL=set_tracking_url.test.js.map