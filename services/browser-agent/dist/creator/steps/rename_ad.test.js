"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const rename_ad_js_1 = require("./rename_ad.js");
// Идемпотентность переименования объявления.
(0, node_test_1.describe)('RenameAdStep', () => {
    (0, node_test_1.it)('isSatisfied когда есть to и нет from', () => {
        const s = new rename_ad_js_1.RenameAdStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['Ad_v2'] }, { from: 'Ad', to: 'Ad_v2' }), true);
    });
});
//# sourceMappingURL=rename_ad.test.js.map