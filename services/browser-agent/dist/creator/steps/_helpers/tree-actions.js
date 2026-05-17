"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.createDuplicateStep = createDuplicateStep;
exports.createRenameStep = createRenameStep;
// Фабрики шагов tree-навигации: duplicate/rename для ad и adset
// идентичны по логике — отличаются только role и формулировками ошибок.
const base_js_1 = require("../base.js");
const locator_js_1 = require("../../locator.js");
const humanizer_js_1 = require("../../humanizer.js");
const tree_nav_js_1 = require("./tree-nav.js");
function roleLabel(role) {
    return role === 'ad' ? 'Объявление' : 'Ad set';
}
function createDuplicateStep(name, role) {
    return class extends base_js_1.BaseStep {
        name = name;
        detect() {
            return { kind: 'matched', current: (0, tree_nav_js_1.listTreeNodeNames)(role) };
        }
        isSatisfied(state, input) {
            const names = state.current || [];
            return names.includes(input.newName);
        }
        async run(_s, input) {
            const node = (0, tree_nav_js_1.findTreeNodeByName)(role, input.sourceName);
            if (!node) {
                throw new Error(`${roleLabel(role)} "${input.sourceName}" не найден в дереве`);
            }
            const menu = node.querySelector('button[aria-haspopup="menu"], [data-testid="row-menu"]') ?? node;
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
    };
}
function createRenameStep(name, role) {
    return class extends base_js_1.BaseStep {
        name = name;
        detect() {
            return { kind: 'matched', current: (0, tree_nav_js_1.listTreeNodeNames)(role) };
        }
        isSatisfied(state, input) {
            const names = state.current || [];
            return names.includes(input.to) && !names.includes(input.from);
        }
        async run(_s, input) {
            const node = (0, tree_nav_js_1.findTreeNodeByName)(role, input.from);
            if (!node)
                throw new Error(`${roleLabel(role)} "${input.from}" не найден`);
            await (0, humanizer_js_1.humanClick)(node);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
            // Двойной клик для входа в режим переименования (через humanizer, без байпасов).
            await (0, humanizer_js_1.humanDoubleClick)(node);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
            const input2 = node.querySelector('input[type="text"]') ??
                document.querySelector('[data-testid="rename-input"] input');
            if (!input2)
                throw new Error('Поле переименования не найдено');
            input2.select();
            await (0, humanizer_js_1.humanType)(input2, input.to);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        }
    };
}
//# sourceMappingURL=tree-actions.js.map