"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const switch_to_adset_js_1 = require("./switch_to_adset.js");
// Идемпотентность: уже выбран нужный ad set.
(0, node_test_1.describe)('SwitchToAdsetStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении текущего', () => {
        const s = new switch_to_adset_js_1.SwitchToAdsetStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 'AS1' }, { name: 'AS1' }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 'AS2' }, { name: 'AS1' }), false);
    });
});
//# sourceMappingURL=switch_to_adset.test.js.map