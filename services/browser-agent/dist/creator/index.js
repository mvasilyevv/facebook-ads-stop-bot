"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.api = void 0;
require("./steps/index.js");
const executor_js_1 = require("./executor.js");
const VERSION = '2.0.0';
const api = {
    version: VERSION,
    async run(plan, variables) {
        const emit = (event, payload) => {
            const fn = globalThis.fbAgentEmit;
            if (typeof fn === 'function')
                fn(event, payload);
        };
        return (0, executor_js_1.runPlan)(plan, variables, emit);
    },
    async startRecording(_planName) {
        throw new Error('recorder wired in phase 4');
    },
    async stopRecording() {
        throw new Error('recorder wired in phase 4');
    },
};
exports.api = api;
globalThis.window = globalThis.window ?? {};
globalThis.window.__fbAgent = api;
//# sourceMappingURL=index.js.map