"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.TOGGLE_SELECTOR = void 0;
exports.resolveToggleHandleFromCell = resolveToggleHandleFromCell;
exports.TOGGLE_SELECTOR = '[role="switch"]';
async function resolveToggleHandleFromCell(cell) {
    if (!cell) {
        return null;
    }
    if ((await cell.getAttribute('role')) === 'switch') {
        return cell;
    }
    return cell.$(exports.TOGGLE_SELECTOR);
}
//# sourceMappingURL=toggle-utils.js.map