"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.api = void 0;
require("./steps/index.js");
const executor_js_1 = require("./executor.js");
const recorder_js_1 = require("./recorder.js");
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
    async startRecording(planName) {
        (0, recorder_js_1.startRecording)(planName);
    },
    async stopRecording() {
        return (0, recorder_js_1.stopRecording)();
    },
    getRecorderStatus() {
        return (0, recorder_js_1.getStatus)();
    },
};
exports.api = api;
globalThis.window = globalThis.window ?? {};
globalThis.window.__fbAgent = api;
//# sourceMappingURL=index.js.map