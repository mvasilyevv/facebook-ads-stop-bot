"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetOptimizationGoalStep = void 0;
// Шаг: выбор цели оптимизации (Optimization goal).
const base_js_1 = require("./base.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const SPEC = {
    block: {
        testid: 'optimization-goal',
        aria: ['Цель оптимизации', 'Optimization goal', 'Performance goal'],
        text: ['цель оптимизации', 'optimization goal'],
    },
    labels: index_js_1.optimizationGoalLabels,
};
class SetOptimizationGoalStep extends base_js_1.BaseStep {
    name = 'set_optimization_goal';
    async detect(_ctx) {
        const current = (0, select_from_dropdown_js_1.readSelectedValue)(SPEC);
        return current ? { kind: 'matched', current } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        return state.kind === 'matched' && state.current === input.value;
    }
    async run(_state, input) {
        await (0, select_from_dropdown_js_1.selectValue)(SPEC, input.value);
    }
}
exports.SetOptimizationGoalStep = SetOptimizationGoalStep;
//# sourceMappingURL=set_optimization_goal.js.map