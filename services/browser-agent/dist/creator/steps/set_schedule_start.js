"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetScheduleStartStep = void 0;
// Шаг: задание даты/времени старта расписания (datetime-local input).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const BLOCK = {
    testid: 'schedule-start',
    aria: ['Дата начала', 'Start date', 'Schedule start'],
    text: ['дата начала', 'start date'],
};
function readStart() {
    const block = (0, locator_js_1.findBlock)(BLOCK);
    if (!block)
        return null;
    const field = block.querySelector('input[type="datetime-local"], input[type="date"], input[name*="start"]');
    return field?.value?.trim() || null;
}
class SetScheduleStartStep extends base_js_1.BaseStep {
    name = 'set_schedule_start';
    detect() {
        const cur = readStart();
        return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        return state.kind === 'matched' && state.current === input.isoDate;
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Schedule start не найден');
        const field = block.querySelector('input[type="datetime-local"], input[type="date"], input[name*="start"]');
        if (!field)
            throw new Error('Поле даты начала не найдено');
        await (0, humanizer_js_1.humanClick)(field);
        field.select();
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        await (0, humanizer_js_1.humanType)(field, input.isoDate);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.SetScheduleStartStep = SetScheduleStartStep;
//# sourceMappingURL=set_schedule_start.js.map