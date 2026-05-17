"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CreateAdsetStep = void 0;
// Шаг: создание ad set (ввод имени в форме адсета).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const NAME_BLOCK = {
    testid: 'adset-name',
    aria: ['Название группы объявлений', 'Ad set name'],
};
function readName() {
    const block = (0, locator_js_1.findBlock)(NAME_BLOCK);
    if (!block)
        return null;
    const input = block.querySelector('input[type="text"]');
    return input?.value || null;
}
class CreateAdsetStep extends base_js_1.BaseStep {
    name = 'create_adset';
    detect() {
        const name = readName();
        return name ? { kind: 'matched', current: { name } } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        const c = state.current;
        return !!c && c.name === input.name;
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(NAME_BLOCK);
        if (!block)
            throw new Error('Блок Ad set name не найден');
        const field = block.querySelector('input[type="text"]');
        if (!field)
            throw new Error('Поле имени ad set не найдено');
        await (0, humanizer_js_1.humanClick)(field);
        field.select();
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        await (0, humanizer_js_1.humanType)(field, input.name);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.CreateAdsetStep = CreateAdsetStep;
//# sourceMappingURL=create_adset.js.map