"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_schedule_start_js_1 = require("./set_schedule_start.js");
// Идемпотентность по ISO-дате.
(0, node_test_1.describe)('SetScheduleStartStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении ISO даты', () => {
        const s = new set_schedule_start_js_1.SetScheduleStartStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: '2026-05-17T09:00' }, { isoDate: '2026-05-17T09:00' }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: '2026-05-17T09:00' }, { isoDate: '2026-05-18T09:00' }), false);
    });
});
//# sourceMappingURL=set_schedule_start.test.js.map