"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.UnknownStep = void 0;
// Placeholder для нераспознанных шагов — всегда падает с описательным сообщением.
const base_js_1 = require("./base.js");
class UnknownStep extends base_js_1.BaseStep {
    name = 'unknown';
    detect() {
        return { kind: 'unknown' };
    }
    isSatisfied() {
        return false;
    }
    async run(_s, input, _ctx) {
        throw new Error(`UnimplementedStepError: запиши новый шаг для raw=${JSON.stringify(input.raw)}`);
    }
}
exports.UnknownStep = UnknownStep;
//# sourceMappingURL=unknown.js.map