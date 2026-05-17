"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const click_next_js_1 = require("./click_next.js");
// click_next — переходный шаг, isSatisfied всегда false.
(0, node_test_1.describe)('ClickNextStep', () => {
    (0, node_test_1.it)('isSatisfied всегда false', () => {
        const s = new click_next_js_1.ClickNextStep();
        node_assert_1.default.equal(s.isSatisfied(), false);
    });
});
//# sourceMappingURL=click_next.test.js.map