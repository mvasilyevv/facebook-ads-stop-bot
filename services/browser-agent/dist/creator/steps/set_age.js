"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetAgeStep = void 0;
// Шаг: установка диапазона возраста через два дропдауна (min, max).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const BLOCK = { aria: ['Возраст', 'Age'], text: ['возраст', 'age'] };
function parseAriaNum(el) {
    if (!el)
        return NaN;
    const raw = el.getAttribute('aria-label') ?? el.value ?? '';
    const m = raw.match(/\d+/);
    return m ? Number(m[0]) : NaN;
}
function readRange() {
    const block = (0, locator_js_1.findBlock)(BLOCK);
    if (!block)
        return null;
    const minSel = block.querySelector('[data-testid="age-min"] [aria-label]') ??
        block.querySelector('select[name*="min"]');
    const maxSel = block.querySelector('[data-testid="age-max"] [aria-label]') ??
        block.querySelector('select[name*="max"]');
    const min = parseAriaNum(minSel);
    const max = parseAriaNum(maxSel);
    return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
}
async function pickFromDropdown(trigger, value) {
    await (0, humanizer_js_1.humanClick)(trigger);
    await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
    const option = Array.from(document.querySelectorAll('[role="option"]')).find((el) => (el.textContent || '').trim() === String(value));
    if (!option)
        throw new Error(`Опция ${value} не найдена`);
    await (0, humanizer_js_1.humanClick)(option);
}
class SetAgeStep extends base_js_1.BaseStep {
    name = 'set_age';
    detect() {
        const cur = readRange();
        return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        const c = state.current;
        return !!c && c.min === input.min && c.max === input.max;
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Age не найден');
        const minTrigger = block.querySelector('[data-testid="age-min"] button, button[aria-label*="мин"], button[aria-label*="min"]');
        const maxTrigger = block.querySelector('[data-testid="age-max"] button, button[aria-label*="макс"], button[aria-label*="max"]');
        if (!minTrigger || !maxTrigger)
            throw new Error('Триггеры возраста не найдены');
        await pickFromDropdown(minTrigger, input.min);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        await pickFromDropdown(maxTrigger, input.max);
    }
}
exports.SetAgeStep = SetAgeStep;
//# sourceMappingURL=set_age.js.map