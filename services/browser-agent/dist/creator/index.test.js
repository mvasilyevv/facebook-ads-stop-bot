"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
(0, node_test_1.describe)('creator entrypoint', () => {
    (0, node_test_1.it)('installs window.__fbAgent', async () => {
        const win = {};
        globalThis.window = win;
        await Promise.resolve().then(() => __importStar(require('./index.js')));
        node_assert_1.default.ok(win.__fbAgent, 'window.__fbAgent должен быть установлен');
        node_assert_1.default.equal(typeof win.__fbAgent.run, 'function');
        node_assert_1.default.equal(typeof win.__fbAgent.startRecording, 'function');
        node_assert_1.default.equal(typeof win.__fbAgent.stopRecording, 'function');
    });
    (0, node_test_1.it)('window.__fbAgent.run делегирует в runPlan', async () => {
        const win = globalThis.window;
        const result = await win.__fbAgent.run({ schema_version: 1, steps: [] }, {});
        node_assert_1.default.equal(result.ok, true);
    });
});
//# sourceMappingURL=index.test.js.map