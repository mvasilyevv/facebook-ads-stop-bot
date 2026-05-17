"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RenameAdStep = void 0;
// Шаг: переименование объявления (аналогично rename_adset, role=ad).
const base_js_1 = require("./base.js");
const humanizer_js_1 = require("../humanizer.js");
const tree_nav_js_1 = require("./_helpers/tree-nav.js");
class RenameAdStep extends base_js_1.BaseStep {
    name = 'rename_ad';
    detect() {
        return { kind: 'matched', current: (0, tree_nav_js_1.listTreeNodeNames)('ad') };
    }
    isSatisfied(state, input) {
        const names = state.current || [];
        return names.includes(input.to) && !names.includes(input.from);
    }
    async run(_s, input) {
        const node = (0, tree_nav_js_1.findTreeNodeByName)('ad', input.from);
        if (!node)
            throw new Error(`Объявление "${input.from}" не найдено`);
        await (0, humanizer_js_1.humanClick)(node);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        const rect = node.getBoundingClientRect();
        node.dispatchEvent(new MouseEvent('dblclick', {
            bubbles: true,
            clientX: rect.left + rect.width / 2,
            clientY: rect.top + rect.height / 2,
        }));
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        const input2 = node.querySelector('input[type="text"]') ??
            document.querySelector('[data-testid="rename-input"] input');
        if (!input2)
            throw new Error('Поле переименования не найдено');
        input2.select();
        await (0, humanizer_js_1.humanType)(input2, input.to);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.RenameAdStep = RenameAdStep;
//# sourceMappingURL=rename_ad.js.map