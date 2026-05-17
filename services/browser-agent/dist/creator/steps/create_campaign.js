"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CreateCampaignStep = void 0;
// Шаг: создание кампании (запуск wizard, ввод имени, выбор objective).
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const humanizer_js_1 = require("../humanizer.js");
const index_js_1 = require("../enums/index.js");
const select_from_dropdown_js_1 = require("./_helpers/select-from-dropdown.js");
const OBJECTIVE_SPEC = {
    block: {
        testid: 'campaign-objective',
        aria: ['Цель кампании', 'Campaign objective'],
        text: ['цель кампании', 'campaign objective'],
    },
    labels: index_js_1.objectiveLabels,
};
const NAME_BLOCK = {
    testid: 'campaign-name',
    aria: ['Название кампании', 'Campaign name'],
};
function readName() {
    const block = (0, locator_js_1.findBlock)(NAME_BLOCK);
    if (!block)
        return null;
    const input = block.querySelector('input[type="text"]');
    return input?.value || null;
}
class CreateCampaignStep extends base_js_1.BaseStep {
    name = 'create_campaign';
    detect() {
        const name = readName();
        return name
            ? { kind: 'matched', current: { name } }
            : { kind: 'missing' };
    }
    isSatisfied(state, input) {
        const c = state.current;
        return !!c && c.name === input.name;
    }
    async run(_s, input) {
        // Если кнопка «Создать» доступна — нажимаем (иначе предполагаем что мы уже в wizard).
        const createBtn = (0, locator_js_1.findByAriaLabel)(['Создать', 'Create']) ?? (0, locator_js_1.findByNormalizedText)(['создать', 'create']);
        if (createBtn) {
            await (0, humanizer_js_1.humanClick)(createBtn);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        }
        await (0, select_from_dropdown_js_1.selectValue)(OBJECTIVE_SPEC, input.objective);
        const block = (0, locator_js_1.findBlock)(NAME_BLOCK);
        if (block) {
            const field = block.querySelector('input[type="text"]');
            if (field) {
                await (0, humanizer_js_1.humanClick)(field);
                field.select();
                await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.SHORT);
                await (0, humanizer_js_1.humanType)(field, input.name);
                await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
            }
        }
    }
}
exports.CreateCampaignStep = CreateCampaignStep;
//# sourceMappingURL=create_campaign.js.map