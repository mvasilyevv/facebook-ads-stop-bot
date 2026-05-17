"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.findByTestId = findByTestId;
exports.findByAriaLabel = findByAriaLabel;
exports.findByFiberRole = findByFiberRole;
exports.findByNormalizedText = findByNormalizedText;
exports.findBlock = findBlock;
// Структурный locator: testid → fiber-role → aria → нормализованный текст fallback.
const text_js_1 = require("./text.js");
const fiber_js_1 = require("./fiber.js");
function findByTestId(testid, root = document) {
    return root.querySelector(`[data-testid="${CSS.escape(testid)}"]`);
}
function findByAriaLabel(labels, root = document) {
    const targets = new Set(labels.map(text_js_1.normalizeText));
    for (const el of Array.from(root.querySelectorAll('[aria-label]'))) {
        const aria = (0, text_js_1.normalizeText)(el.getAttribute('aria-label') || '');
        if (targets.has(aria))
            return el;
    }
    return null;
}
function findByFiberRole(role, root = document) {
    for (const el of Array.from(root.querySelectorAll('*'))) {
        const props = (0, fiber_js_1.getReactProps)(el);
        if (props && props.role === role)
            return el;
    }
    return null;
}
function findByNormalizedText(texts, root = document) {
    const targets = new Set(texts.map(text_js_1.normalizeText));
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let cur = walker.currentNode;
    while (cur) {
        const direct = Array.from(cur.childNodes)
            .filter((n) => n.nodeType === Node.TEXT_NODE)
            .map((n) => (0, text_js_1.normalizeText)(n.textContent || ''))
            .join(' ')
            .trim();
        if (direct && targets.has(direct))
            return cur;
        cur = walker.nextNode();
    }
    return null;
}
function findBlock(spec, root = document) {
    if (spec.testid) {
        const el = findByTestId(spec.testid, root);
        if (el)
            return el;
    }
    if (spec.fiberRole) {
        const el = findByFiberRole(spec.fiberRole, root);
        if (el)
            return el;
    }
    if (spec.aria?.length) {
        const el = findByAriaLabel(spec.aria, root);
        if (el)
            return el;
    }
    if (spec.text?.length) {
        const el = findByNormalizedText(spec.text, root);
        if (el)
            return el;
    }
    return null;
}
//# sourceMappingURL=locator.js.map