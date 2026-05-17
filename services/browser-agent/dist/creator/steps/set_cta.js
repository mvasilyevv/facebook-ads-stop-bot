"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetCtaStep = void 0;
// Шаг: выбор Call-To-Action в карточке объявления.
const base_js_1 = require("./base.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const SPEC = {
    block: {
        testid: 'call-to-action',
        aria: ['Призыв к действию', 'Call to action'],
        text: ['призыв к действию', 'call to action'],
    },
    labels: index_js_1.ctaLabels,
};
class SetCtaStep extends base_js_1.BaseStep {
    name = 'set_cta';
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
exports.SetCtaStep = SetCtaStep;
//# sourceMappingURL=set_cta.js.map