"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getFiber = getFiber;
exports.getReactProps = getReactProps;
exports.walkUp = walkUp;
// Чтение React internals (__reactFiber$* / __reactProps$*) по динамическому ключу.
function findKey(el, prefix) {
    for (const key of Object.keys(el)) {
        if (key.startsWith(prefix))
            return key;
    }
    return null;
}
function getFiber(el) {
    const key = findKey(el, '__reactFiber$');
    return key ? el[key] : null;
}
function getReactProps(el) {
    const key = findKey(el, '__reactProps$');
    return key ? el[key] : null;
}
function walkUp(el, predicate, maxDepth = 12) {
    let cur = el;
    let depth = 0;
    while (cur && depth < maxDepth) {
        if (predicate(cur))
            return cur;
        cur = cur.parentElement;
        depth++;
    }
    return null;
}
//# sourceMappingURL=fiber.js.map