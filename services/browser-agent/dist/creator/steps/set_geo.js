"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SetGeoStep = void 0;
// Шаг: мульти-выбор гео (страны) с автокомплитом.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const BLOCK = {
    testid: 'locations',
    aria: ['Места', 'Locations'],
    text: ['места', 'locations'],
};
function readCurrentCountries() {
    const block = (0, locator_js_1.findBlock)(BLOCK);
    if (!block)
        return [];
    return Array.from(block.querySelectorAll('[data-testid="selected-country"], [aria-label^="Удалить"]'))
        .map((el) => (el.getAttribute('data-country') || el.textContent || '').trim())
        .filter(Boolean);
}
class SetGeoStep extends base_js_1.BaseStep {
    name = 'set_geo';
    detect() {
        return { kind: 'matched', current: readCurrentCountries() };
    }
    isSatisfied(state, input) {
        const cur = new Set(state.current || []);
        return input.countries.every((c) => cur.has(c));
    }
    async run(_s, input) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Locations не найден');
        const search = block.querySelector('input[type="text"], input[type="search"]');
        if (!search)
            throw new Error('Поле поиска стран не найдено');
        const cur = new Set(readCurrentCountries());
        for (const code of input.countries) {
            if (cur.has(code))
                continue;
            await (0, humanizer_js_1.humanType)(search, code);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
            const option = document.querySelector(`[role="option"][data-country="${code}"]`) ??
                document.querySelector('[role="option"]');
            if (!option)
                throw new Error(`Страна ${code} не найдена в подсказках`);
            await (0, humanizer_js_1.humanClick)(option);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
        }
    }
}
exports.SetGeoStep = SetGeoStep;
//# sourceMappingURL=set_geo.js.map