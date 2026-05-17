"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetAttributionStep = void 0;
// Шаг: выбор окна атрибуции (Attribution setting).
const base_js_1 = require("./base.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const SPEC = {
    block: {
        testid: 'attribution-setting',
        aria: ['Окно атрибуции', 'Attribution setting', 'Attribution window'],
        text: ['окно атрибуции', 'attribution'],
    },
    labels: index_js_1.attributionLabels,
};
class SetAttributionStep extends base_js_1.BaseStep {
    name = 'set_attribution';
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
exports.SetAttributionStep = SetAttributionStep;
//# sourceMappingURL=set_attribution.js.map