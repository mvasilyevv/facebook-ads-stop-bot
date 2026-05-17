"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.UploadCreativesStep = void 0;
// Шаг: загрузка креативов. Эмитит request_upload, Python хост вызывает setInputFiles.
const base_js_1 = require("./base.js");
const locator_js_1 = require("../locator.js");
const BLOCK = {
    testid: 'media-section',
    aria: ['Медиа', 'Media'],
    text: ['медиа', 'media'],
};
class UploadCreativesStep extends base_js_1.BaseStep {
    name = 'upload_creatives';
    detect() {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
        return { kind: 'matched', current: thumbs.length };
    }
    isSatisfied(state, input) {
        return state.current === input.paths.length;
    }
    async run(_s, input, ctx) {
        const block = (0, locator_js_1.findBlock)(BLOCK);
        if (!block)
            throw new Error('Блок Media не найден');
        const fileInput = block.querySelector('input[type="file"]');
        if (!fileInput)
            throw new Error('input[type=file] не найден в блоке Media');
        const id = `upload-${Date.now()}`;
        fileInput.setAttribute('data-fb-upload-id', id);
        ctx.emit('request_upload', {
            id,
            paths: input.paths,
            selector: `input[data-fb-upload-id="${id}"]`,
        });
    }
}
exports.UploadCreativesStep = UploadCreativesStep;
//# sourceMappingURL=upload_creatives.js.map