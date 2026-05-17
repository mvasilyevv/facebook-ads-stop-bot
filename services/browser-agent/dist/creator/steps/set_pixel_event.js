"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetPixelEventStep = void 0;
// Шаг: выбор пикселя по ID (поиск + клик) и события Pixel (Purchase/Lead/...).
const base_js_1 = require("./base.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const EVENT_SPEC = {
    block: {
        testid: 'pixel-event',
        aria: ['Событие конверсии', 'Conversion event'],
        text: ['событие конверсии', 'conversion event'],
    },
    labels: index_js_1.pixelEventLabels,
};
const PIXEL_BLOCK = {
    testid: 'pixel-selector',
    aria: ['Источник данных', 'Data source', 'Пиксель'],
    text: ['пиксель', 'data source'],
};
function readCurrentPixelId() {
    const block = (0, locator_js_1.findBlock)(PIXEL_BLOCK);
    if (!block)
        return null;
    const id = block.querySelector('[data-pixel-id]')?.getAttribute('data-pixel-id');
    return id ?? null;
}
class SetPixelEventStep extends base_js_1.BaseStep {
    name = 'set_pixel_event';
    async detect(_ctx) {
        const event = (0, select_from_dropdown_js_1.readSelectedValue)(EVENT_SPEC);
        const pixelId = readCurrentPixelId();
        if (event && pixelId) {
            return { kind: 'matched', current: { event, pixelId } };
        }
        return { kind: 'missing' };
    }
    isSatisfied(state, input) {
        if (state.kind !== 'matched')
            return false;
        const cur = state.current;
        return cur.event === input.event && cur.pixelId === input.pixelId;
    }
    async run(_state, input) {
        // Выбор пикселя по ID через поле поиска источника данных.
        const pxBlock = (0, locator_js_1.findBlock)(PIXEL_BLOCK);
        if (!pxBlock)
            throw new Error('Не найден блок выбора Pixel в UI');
        const trigger = pxBlock.querySelector('button[aria-haspopup="listbox"], [role="combobox"]');
        if (!trigger)
            throw new Error('Не найден триггер открытия списка Pixel');
        await (0, humanizer_js_1.humanClick)(trigger);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        const search = document.querySelector('input[role="combobox"], input[type="search"]');
        if (search) {
            await (0, humanizer_js_1.humanType)(search, input.pixelId);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
            const option = document.querySelector(`[role="option"][data-pixel-id="${input.pixelId}"]`) ?? document.querySelector('[role="option"]');
            if (!option)
                throw new Error(`Пиксель ${input.pixelId} не найден в списке`);
            await (0, humanizer_js_1.humanClick)(option);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        }
        await (0, select_from_dropdown_js_1.selectValue)(EVENT_SPEC, input.event);
    }
}
exports.SetPixelEventStep = SetPixelEventStep;
//# sourceMappingURL=set_pixel_event.js.map