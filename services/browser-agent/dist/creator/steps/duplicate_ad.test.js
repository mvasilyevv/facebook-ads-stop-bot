"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const duplicate_ad_js_1 = require("./duplicate_ad.js");
// Идемпотентность: уже есть newName в списке объявлений.
(0, node_test_1.describe)('DuplicateAdStep', () => {
    (0, node_test_1.it)('isSatisfied когда newName уже есть в дереве', () => {
        const s = new duplicate_ad_js_1.DuplicateAdStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['Ad1', 'Ad2'] }, { sourceName: 'Ad1', newName: 'Ad2' }), true);
    });
});
//# sourceMappingURL=duplicate_ad.test.js.map