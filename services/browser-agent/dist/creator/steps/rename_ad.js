"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RenameAdStep = void 0;
// Шаг: переименование объявления. Идемпотентен если уже есть to и нет from.
const tree_actions_js_1 = require("./_helpers/tree-actions.js");
exports.RenameAdStep = (0, tree_actions_js_1.createRenameStep)('rename_ad', 'ad');
//# sourceMappingURL=rename_ad.js.map