"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DuplicateAdStep = void 0;
// Шаг: дублирование объявления. Идемпотентен если в дереве уже есть newName.
const tree_actions_js_1 = require("./_helpers/tree-actions.js");
exports.DuplicateAdStep = (0, tree_actions_js_1.createDuplicateStep)('duplicate_ad', 'ad');
//# sourceMappingURL=duplicate_ad.js.map