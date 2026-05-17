"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SaveDraftStep = void 0;
// Шаг: «Сохранить черновик». Идемпотентен если уже показан индикатор «Сохранено».
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
function hasSavedIndicator() {
    const aria = (0, locator_js_1.findByAriaLabel)(['Сохранено', 'Saved', 'Черновик сохранён']);
    if (aria)
        return true;
    const text = (0, locator_js_1.findByNormalizedText)(['сохранено', 'saved', 'черновик сохранён']);
    return !!text;
}
class SaveDraftStep extends base_js_1.BaseStep {
    name = 'save_draft';
    detect() {
        return hasSavedIndicator()
            ? { kind: 'matched', current: 'saved' }
            : { kind: 'missing' };
    }
    isSatisfied(state) {
        return state.kind === 'matched' && state.current === 'saved';
    }
    async run() {
        const btn = (0, locator_js_1.findByAriaLabel)(['Сохранить черновик', 'Save draft', 'Сохранить']) ??
            (0, locator_js_1.findByNormalizedText)(['сохранить черновик', 'save draft']);
        if (!btn)
            throw new Error('Кнопка «Сохранить черновик» не найдена');
        await (0, humanizer_js_1.humanClick)(btn);
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_SCENES);
    }
}
exports.SaveDraftStep = SaveDraftStep;
//# sourceMappingURL=save_draft.js.map