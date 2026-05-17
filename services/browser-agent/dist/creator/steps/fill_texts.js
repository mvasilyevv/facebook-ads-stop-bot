"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FillTextsStep = void 0;
// Шаг: заполнение текстовых полей объявления (Primary text, Headline, Description).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const PRIMARY = {
    testid: 'primary-text',
    aria: ['Основной текст', 'Primary text'],
};
const HEADLINE = {
    testid: 'headline',
    aria: ['Заголовок', 'Headline'],
};
const DESCRIPTION = {
    testid: 'description',
    aria: ['Описание', 'Description'],
};
function readField(block) {
    const el = (0, locator_js_1.findBlock)(block);
    if (!el)
        return null;
    const input = el.querySelector('textarea, input[type="text"], [contenteditable="true"]');
    if (!input)
        return null;
    if ('value' in input && typeof input.value === 'string')
        return input.value;
    return (input.textContent || '').trim();
}
async function fillBlock(block, value) {
    const el = (0, locator_js_1.findBlock)(block);
    if (!el)
        throw new Error(`Блок не найден: ${JSON.stringify(block)}`);
    const field = el.querySelector('textarea, input[type="text"]');
    if (!field)
        throw new Error('Поле текста не найдено');
    await (0, humanizer_js_1.humanClick)(field);
    field.select?.();
    await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
    await (0, humanizer_js_1.humanType)(field, value);
    await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
}
class FillTextsStep extends base_js_1.BaseStep {
    name = 'fill_texts';
    detect() {
        const current = {
            primary: readField(PRIMARY) ?? '',
            headline: readField(HEADLINE) ?? '',
            description: readField(DESCRIPTION) ?? '',
        };
        return { kind: 'matched', current };
    }
    isSatisfied(state, input) {
        const c = state.current;
        if (!c)
            return false;
        if (c.primary !== input.primary)
            return false;
        if (c.headline !== input.headline)
            return false;
        if (input.description !== undefined && c.description !== input.description)
            return false;
        return true;
    }
    async run(_s, input) {
        await fillBlock(PRIMARY, input.primary);
        await fillBlock(HEADLINE, input.headline);
        if (input.description)
            await fillBlock(DESCRIPTION, input.description);
    }
}
exports.FillTextsStep = FillTextsStep;
//# sourceMappingURL=fill_texts.js.map