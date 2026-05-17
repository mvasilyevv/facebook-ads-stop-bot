"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RenameAdsetStep = void 0;
// Шаг: переименование ad set. Идемпотентен если уже есть to и нет from.
const tree_actions_js_1 = require("./_helpers/tree-actions.js");
exports.RenameAdsetStep = (0, tree_actions_js_1.createRenameStep)('rename_adset', 'adset');
//# sourceMappingURL=rename_adset.js.map