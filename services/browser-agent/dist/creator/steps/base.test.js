"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const base_js_1 = require("./base.js");
class FakeStep extends base_js_1.BaseStep {
    name = 'fake';
    executed = false;
    detect(_ctx) {
        return { kind: 'matched', current: 'A' };
    }
    isSatisfied(state, input) {
        return state.current === input.value;
    }
    async run() {
        this.executed = true;
    }
}
const ctx = { variables: {}, emit: () => { } };
(0, node_test_1.describe)('BaseStep', () => {
    (0, node_test_1.it)('skip когда уже satisfied', async () => {
        const s = new FakeStep();
        const state = s.detect(ctx);
        await s.execute(state, { value: 'A' }, ctx);
        node_assert_1.default.equal(s.executed, false);
    });
    (0, node_test_1.it)('исполняет когда не satisfied', async () => {
        const s = new FakeStep();
        const state = s.detect(ctx);
        await s.execute(state, { value: 'B' }, ctx);
        node_assert_1.default.equal(s.executed, true);
    });
});
//# sourceMappingURL=base.test.js.map