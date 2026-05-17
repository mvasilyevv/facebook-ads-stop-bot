"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.listTreeNodeNames = listTreeNodeNames;
exports.findTreeNodeByName = findTreeNodeByName;
// Хелперы для работы с левой панелью (дерево кампаний/адсетов/объявлений).
const text_js_1 = require("../../text.js");
function listTreeNodeNames(role) {
    const nodes = Array.from(document.querySelectorAll(`[data-tree-role="${role}"], [data-testid="${role}-node"]`));
    return nodes
        .map((el) => (el.getAttribute('data-name') || el.textContent || '').trim())
        .filter(Boolean);
}
function findTreeNodeByName(role, name) {
    const target = (0, text_js_1.normalizeText)(name);
    const nodes = Array.from(document.querySelectorAll(`[data-tree-role="${role}"], [data-testid="${role}-node"]`));
    for (const node of nodes) {
        const txt = (0, text_js_1.normalizeText)(node.getAttribute('data-name') || node.textContent || '');
        if (txt === target)
            return node;
    }
    return null;
}
//# sourceMappingURL=tree-nav.js.map