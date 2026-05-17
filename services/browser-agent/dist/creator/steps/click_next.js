"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ClickNextStep = void 0;
// Шаг: кнопка «Далее» / «Next» в wizard. Переходный, никогда не satisfied.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
class ClickNextStep extends base_js_1.BaseStep {
    name = 'click_next';
    detect() {
        return { kind: 'matched' };
    }
    isSatisfied() {
        return false;
    }
    async run() {
        const btn = (0, locator_js_1.findByAriaLabel)(['Далее', 'Next', 'Продолжить', 'Continue']) ??
            (0, locator_js_1.findByNormalizedText)(['далее', 'next', 'продолжить', 'continue']);
        if (!btn)
            throw new Error('Кнопка «Далее» не найдена');
        await (0, humanizer_js_1.humanClick)(btn);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_SCENES);
    }
}
exports.ClickNextStep = ClickNextStep;
//# sourceMappingURL=click_next.js.map