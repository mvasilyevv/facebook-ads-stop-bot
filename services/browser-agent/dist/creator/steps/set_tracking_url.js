"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetTrackingUrlStep = void 0;
// Шаг: ввод URL для отслеживания (Tracking URL parameters).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const BLOCK = {
    testid: 'tracking-url',
    aria: ['URL для отслеживания', 'Tracking URL', 'URL parameters'],
    text: ['url для отслеживания', 'tracking url'],
};
function readUrl() {
    const block = (0, locator_js_1.findBlock)(BLOCK);
    if (!block)
        return null;
    const input = block.querySelector('input[type="text"], input[type="url"], textarea');
    return input?.value?.trim() || null;
}
class SetTrackingUrlStep extends base_js_1.BaseStep {
    name = 'set_tracking_url';
    detect() {
        const cur = readUrl();
        return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        return state.kind === 'matched' && state.current === input.url;
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Tracking URL не найден');
        const field = block.querySelector('input[type="text"], input[type="url"], textarea');
        if (!field)
            throw new Error('Поле URL не найдено');
        await (0, humanizer_js_1.humanClick)(field);
        field.select();
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        await (0, humanizer_js_1.humanType)(field, input.url);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
}
exports.SetTrackingUrlStep = SetTrackingUrlStep;
//# sourceMappingURL=set_tracking_url.js.map