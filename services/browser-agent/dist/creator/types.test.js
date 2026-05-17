"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
(0, node_test_1.describe)('creator types', () => {
    (0, node_test_1.it)('compiles without errors', () => {
        const _state = { kind: 'unknown' };
        const _ev = { type: 'click', selector: '.x', text: '', value: null };
        const _ctx = { variables: {}, emit: () => { } };
        node_assert_1.default.ok(true);
    });
});
//# sourceMappingURL=types.test.js.map