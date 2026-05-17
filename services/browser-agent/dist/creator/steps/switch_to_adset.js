"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SwitchToAdsetStep = void 0;
// Шаг: переключение на конкретный ad set в дереве слева.
const base_js_1 = require("./base.js");
const humanizer_js_1 = require("../humanizer.js");
const tree_nav_js_1 = require("./_helpers/tree-nav.js");
function currentAdsetName() {
    const sel = document.querySelector('[data-tree-role="adset"][aria-selected="true"], [data-testid="adset-node"][aria-current="true"]');
    if (!sel)
        return null;
    return (sel.getAttribute('data-name') || sel.textContent || '').trim();
}
class SwitchToAdsetStep extends base_js_1.BaseStep {
    name = 'switch_to_adset';
    detect() {
        const cur = currentAdsetName();
        return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        return state.kind === 'matched' && state.current === input.name;
    }
    async run(_s, input) {
        const node = (0, tree_nav_js_1.findTreeNodeByName)('adset', input.name);
        if (!node)
            throw new Error(`Ad set "${input.name}" не найден в дереве`);
        await (0, humanizer_js_1.humanClick)(node);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.SwitchToAdsetStep = SwitchToAdsetStep;
//# sourceMappingURL=switch_to_adset.js.map