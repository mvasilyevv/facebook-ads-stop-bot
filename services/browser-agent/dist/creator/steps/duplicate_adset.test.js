"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const duplicate_adset_js_1 = require("./duplicate_adset.js");
// Идемпотентность: уже есть newName в списке адсетов.
(0, node_test_1.describe)('DuplicateAdsetStep', () => {
    (0, node_test_1.it)('isSatisfied когда newName уже есть в дереве', () => {
        const s = new duplicate_adset_js_1.DuplicateAdsetStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['AS1', 'AS2'] }, { sourceName: 'AS1', newName: 'AS2' }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['AS1'] }, { sourceName: 'AS1', newName: 'AS2' }), false);
    });
});
//# sourceMappingURL=duplicate_adset.test.js.map