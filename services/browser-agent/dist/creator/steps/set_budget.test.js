"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_budget_js_1 = require("./set_budget.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность бюджета (по сумме).
(0, node_test_1.describe)('SetBudgetStep', () => {
    (0, node_test_1.it)('isSatisfied при равной сумме', () => {
        const s = new set_budget_js_1.SetBudgetStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { amount: 50, currency: 'USD' } }, { amount: 50, currency: index_js_1.Currency.USD }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { amount: 50, currency: 'USD' } }, { amount: 100, currency: index_js_1.Currency.USD }), false);
    });
});
//# sourceMappingURL=set_budget.test.js.map