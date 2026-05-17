"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const executor_js_1 = require("./executor.js");
const registry_js_1 = require("./registry.js");
const base_js_1 = require("./steps/base.js");
class Capture extends base_js_1.BaseStep {
    name = 'cap';
    received = null;
    detect() {
        return { kind: 'unknown' };
    }
    isSatisfied() {
        return false;
    }
    async run(_s, i) {
        this.received = i.v;
    }
}
(0, node_test_1.describe)('executor', () => {
    (0, node_test_1.beforeEach)(() => (0, registry_js_1.clearRegistry)());
    (0, node_test_1.it)('interpolate подставляет {{geo}}', () => {
        const out = (0, executor_js_1.interpolate)({ v: '{{geo}}-{{offer.code}}' }, { geo: 'DE', offer: { code: 'CR2' } });
        node_assert_1.default.deepEqual(out, { v: 'DE-CR2' });
    });
    (0, node_test_1.it)('runPlan выполняет шаги по очереди и эмитит events', async () => {
        const step = new Capture();
        (0, registry_js_1.registerStep)(step);
        const events = [];
        const result = await (0, executor_js_1.runPlan)({ schema_version: 1, steps: [{ step: 'cap', input: { v: '{{geo}}' } }] }, { geo: 'DE' }, (e, p) => events.push([e, p]));
        node_assert_1.default.equal(result.ok, true);
        node_assert_1.default.equal(step.received, 'DE');
        const types = events.map(([e]) => e);
        node_assert_1.default.ok(types.includes('step_started'));
        node_assert_1.default.ok(types.includes('step_finished'));
    });
    (0, node_test_1.it)('runPlan возвращает {ok:false} при неизвестном шаге', async () => {
        const result = await (0, executor_js_1.runPlan)({ schema_version: 1, steps: [{ step: 'nope', input: {} }] }, {}, () => { });
        node_assert_1.default.equal(result.ok, false);
        node_assert_1.default.match(result.error || '', /unknown step/i);
    });
});
//# sourceMappingURL=executor.test.js.map