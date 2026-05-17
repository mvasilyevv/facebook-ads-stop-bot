"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.resolveLabelToEnum = resolveLabelToEnum;
exports.readSelectedValue = readSelectedValue;
exports.selectValue = selectValue;
// Хелпер выбора значения из выпадающего списка по LabelMap (ru+en синонимы).
// Используется enum-шагами через DropdownSpec.
const humanizer_js_1 = require("../../humanizer.js");
const locator_js_1 = require("../../locator.js");
const text_js_1 = require("../../text.js");
function resolveLabelToEnum(label, labels) {
    const norm = (0, text_js_1.normalizeText)(label);
    for (const [enumKey, syns] of Object.entries(labels)) {
        const all = [...syns.ru, ...syns.en].map(text_js_1.normalizeText);
        if (all.includes(norm))
            return enumKey;
    }
    return null;
}
// Читает текущее выбранное значение в дропдауне и резолвит его в enum.
function readSelectedValue(spec) {
    const block = (0, locator_js_1.findBlock)(spec.block);
    if (!block)
        return null;
    const visible = block.querySelector('[aria-selected="true"], [data-selected="true"], button[aria-haspopup="listbox"]');
    const text = (visible?.textContent ?? '').trim();
    if (!text)
        return null;
    return resolveLabelToEnum(text, spec.labels);
}
// Открывает дропдаун и выбирает опцию, соответствующую enum target.
async function selectValue(spec, target) {
    const block = (0, locator_js_1.findBlock)(spec.block);
    if (!block)
        throw new Error(`Блок не найден: ${JSON.stringify(spec.block)}`);
    const trigger = block.querySelector('button[aria-haspopup="listbox"], [role="combobox"]');
    if (!trigger)
        throw new Error('Trigger дропдауна не найден');
    await (0, humanizer_js_1.humanClick)(trigger);
    await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
    const syns = spec.labels[target];
    if (!syns)
        throw new Error(`Unknown enum value: ${target}`);
    const option = (0, locator_js_1.findByNormalizedText)([...syns.ru, ...syns.en]);
    if (!option) {
        throw new Error(`Опция "${target}" не найдена в дропдауне (синонимы: ${[...syns.ru, ...syns.en].join(', ')})`);
    }
    await (0, humanizer_js_1.humanClick)(option);
    await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
}
//# sourceMappingURL=select-from-dropdown.js.map