"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DuplicateAdStep = void 0;
// Шаг: дублирование объявления. Идемпотентен если в дереве уже есть newName.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const tree_nav_js_1 = require("./_helpers/tree-nav.js");
class DuplicateAdStep extends base_js_1.BaseStep {
    name = 'duplicate_ad';
    detect() {
        return { kind: 'matched', current: (0, tree_nav_js_1.listTreeNodeNames)('ad') };
    }
    isSatisfied(state, input) {
        const names = state.current || [];
        return names.includes(input.newName);
    }
    async run(_s, input) {
        const node = (0, tree_nav_js_1.findTreeNodeByName)('ad', input.sourceName);
        if (!node)
            throw new Error(`Объявление "${input.sourceName}" не найдено в дереве`);
        const menu = node.querySelector('button[aria-haspopup="menu"], [data-testid="row-menu"]') ??
            node;
        await (0, humanizer_js_1.humanClick)(menu);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        const dup = (0, locator_js_1.findByAriaLabel)(['Дублировать', 'Duplicate']) ??
            (0, locator_js_1.findByNormalizedText)(['дублировать', 'duplicate']);
        if (!dup)
            throw new Error('Пункт меню «Дублировать» не найден');
        await (0, humanizer_js_1.humanClick)(dup);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        const nameInput = document.querySelector('input[type="text"][name*="name"], [data-testid="duplicate-name"] input');
        if (nameInput) {
            await (0, humanizer_js_1.humanClick)(nameInput);
            nameInput.select();
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
            await (0, humanizer_js_1.humanType)(nameInput, input.newName);
        }
        const confirm = (0, locator_js_1.findByAriaLabel)(['Дублировать', 'Duplicate', 'Подтвердить', 'Confirm']) ??
            (0, locator_js_1.findByNormalizedText)(['дублировать', 'duplicate', 'подтвердить', 'confirm']);
        if (confirm) {
            await (0, humanizer_js_1.humanClick)(confirm);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        }
    }
}
exports.DuplicateAdStep = DuplicateAdStep;
//# sourceMappingURL=duplicate_ad.js.map