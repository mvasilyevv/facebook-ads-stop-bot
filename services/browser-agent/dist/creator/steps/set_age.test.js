"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_age_js_1 = require("./set_age.js");
// Идемпотентность диапазона возраста.
(0, node_test_1.describe)('SetAgeStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении диапазона', () => {
        const s = new set_age_js_1.SetAgeStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { min: 18, max: 65 } }, { min: 18, max: 65 }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { min: 18, max: 65 } }, { min: 25, max: 45 }), false);
    });
});
//# sourceMappingURL=set_age.test.js.map