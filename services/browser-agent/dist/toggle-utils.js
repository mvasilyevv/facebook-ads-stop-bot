"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.resolveToggleHandleFromCell = resolveToggleHandleFromCell;
async function resolveToggleHandleFromCell(cell) {
    if (!cell) {
        return null;
    }
    if ((await cell.getAttribute('role')) === 'switch') {
        return cell;
    }
    return cell.$('[role="switch"]');
}
//# sourceMappingURL=toggle-utils.js.map