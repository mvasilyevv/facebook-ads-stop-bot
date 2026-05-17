"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetConversionLocationStep = void 0;
// Шаг: выбор «Место конверсии» (Conversion Location).
const base_js_1 = require("./base.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const SPEC = {
    block: {
        testid: 'conversion-location',
        aria: ['Место конверсии', 'Conversion location'],
        text: ['место конверсии', 'conversion location'],
    },
    labels: index_js_1.conversionLocationLabels,
};
class SetConversionLocationStep extends base_js_1.BaseStep {
    name = 'set_conversion_location';
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
exports.SetConversionLocationStep = SetConversionLocationStep;
//# sourceMappingURL=set_conversion_location.js.map