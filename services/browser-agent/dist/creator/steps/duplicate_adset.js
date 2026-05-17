"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DuplicateAdsetStep = void 0;
// Шаг: дублирование ad set. Идемпотентен если в дереве уже есть newName.
const tree_actions_js_1 = require("./_helpers/tree-actions.js");
exports.DuplicateAdsetStep = (0, tree_actions_js_1.createDuplicateStep)('duplicate_adset', 'adset');
//# sourceMappingURL=duplicate_adset.js.map