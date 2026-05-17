"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const rename_adset_js_1 = require("./rename_adset.js");
// Идемпотентность: to уже в списке, from удалён.
(0, node_test_1.describe)('RenameAdsetStep', () => {
    (0, node_test_1.it)('isSatisfied когда есть to и нет from', () => {
        const s = new rename_adset_js_1.RenameAdsetStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['AS1_v2', 'AS3'] }, { from: 'AS1', to: 'AS1_v2' }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['AS1', 'AS1_v2'] }, { from: 'AS1', to: 'AS1_v2' }), false);
    });
});
//# sourceMappingURL=rename_adset.test.js.map