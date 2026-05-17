"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RenameAdsetStep = void 0;
// Шаг: переименование ad set. Идемпотентен если уже есть to и нет from.
const base_js_1 = require("./base.js");
const humanizer_js_1 = require("../humanizer.js");
const tree_nav_js_1 = require("./_helpers/tree-nav.js");
class RenameAdsetStep extends base_js_1.BaseStep {
    name = 'rename_adset';
    detect() {
        return { kind: 'matched', current: (0, tree_nav_js_1.listTreeNodeNames)('adset') };
    }
    isSatisfied(state, input) {
        const names = state.current || [];
        return names.includes(input.to) && !names.includes(input.from);
    }
    async run(_s, input) {
        const node = (0, tree_nav_js_1.findTreeNodeByName)('adset', input.from);
        if (!node)
            throw new Error(`Ad set "${input.from}" не найден`);
        await (0, humanizer_js_1.humanClick)(node);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        // Двойной клик / Enter для входа в режим переименования (структурный поиск инпута).
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
exports.RenameAdsetStep = RenameAdsetStep;
//# sourceMappingURL=rename_adset.js.map