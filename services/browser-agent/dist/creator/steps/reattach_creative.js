"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ReattachCreativeStep = void 0;
// Шаг: переприкрепление креативов к существующему объявлению.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const tree_nav_js_1 = require("./_helpers/tree-nav.js");
const humanizer_js_1 = require("../humanizer.js");
const MEDIA_BLOCK = {
    testid: 'media-section',
    aria: ['Медиа', 'Media'],
    text: ['медиа', 'media'],
};
class ReattachCreativeStep extends base_js_1.BaseStep {
    name = 'reattach_creative';
    detect() {
        const block = (0, locator_js_1.findBlock)(MEDIA_BLOCK);
        const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
        return { kind: 'matched', current: thumbs.length };
    }
    isSatisfied(state, input) {
        return state.current === input.paths.length;
    }
    async run(_s, input, ctx) {
        // Переключаемся на объявление в дереве (если найдено).
        const node = (0, tree_nav_js_1.findTreeNodeByName)('ad', input.adName);
        if (node) {
            await (0, humanizer_js_1.humanClick)(node);
            await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
        }
        const block = (0, locator_js_1.findBlock)(MEDIA_BLOCK);
        if (!block)
            throw new Error('Блок Media не найден');
        const fileInput = block.querySelector('input[type="file"]');
        if (!fileInput)
            throw new Error('input[type=file] не найден в блоке Media');
        const id = `reattach-${Date.now()}`;
        fileInput.setAttribute('data-fb-upload-id', id);
        ctx.emit('request_upload', {
            id,
            paths: input.paths,
            selector: `input[data-fb-upload-id="${id}"]`,
        });
    }
}
exports.ReattachCreativeStep = ReattachCreativeStep;
//# sourceMappingURL=reattach_creative.js.map