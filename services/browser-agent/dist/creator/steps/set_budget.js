"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetBudgetStep = void 0;
// Шаг: установка дневного/общего бюджета.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const BLOCK = { aria: ['Бюджет', 'Budget'], text: ['бюджет', 'budget'] };
function readAmount() {
    const block = (0, locator_js_1.findBlock)(BLOCK);
    if (!block)
        return null;
    const input = block.querySelector('input[inputmode="decimal"], input[type="number"], input[name*="budget"]');
    if (!input)
        return null;
    const num = Number(input.value.replace(/[^\d.,]/g, '').replace(',', '.'));
    const cur = (block.querySelector('[aria-label*="валют"], [aria-label*="curren"]')
        ?.textContent || '').trim();
    return Number.isFinite(num) ? { amount: num, currency: cur } : null;
}
class SetBudgetStep extends base_js_1.BaseStep {
    name = 'set_budget';
    detect() {
        const cur = readAmount();
        return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        const c = state.current;
        if (!c || c.amount !== input.amount)
            return false;
        // Если currency задан явно — проверяем совпадение, иначе валюту игнорируем.
        if (input.currency && c.currency !== input.currency)
            return false;
        return true;
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Budget не найден');
        const field = block.querySelector('input[inputmode="decimal"], input[type="number"], input[name*="budget"]');
        if (!field)
            throw new Error('Поле бюджета не найдено');
        await (0, humanizer_js_1.humanClick)(field);
        field.select();
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        await (0, humanizer_js_1.humanType)(field, String(input.amount));
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.SetBudgetStep = SetBudgetStep;
//# sourceMappingURL=set_budget.js.map