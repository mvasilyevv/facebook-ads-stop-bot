"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const create_adset_js_1 = require("./create_adset.js");
// Идемпотентность по имени адсета.
(0, node_test_1.describe)('CreateAdsetStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении имени', () => {
        const s = new create_adset_js_1.CreateAdsetStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { name: 'AS1' } }, { name: 'AS1' }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { name: 'X' } }, { name: 'AS1' }), false);
    });
});
//# sourceMappingURL=create_adset.test.js.map