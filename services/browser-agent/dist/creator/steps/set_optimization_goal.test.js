"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_optimization_goal_js_1 = require("./set_optimization_goal.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность по выбранной цели оптимизации.
(0, node_test_1.describe)('SetOptimizationGoalStep', () => {
    (0, node_test_1.it)('isSatisfied по совпадению значения', () => {
        const s = new set_optimization_goal_js_1.SetOptimizationGoalStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.OptimizationGoal.CONVERSIONS }, { value: index_js_1.OptimizationGoal.CONVERSIONS }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.OptimizationGoal.LINK_CLICKS }, { value: index_js_1.OptimizationGoal.CONVERSIONS }), false);
    });
});
//# sourceMappingURL=set_optimization_goal.test.js.map