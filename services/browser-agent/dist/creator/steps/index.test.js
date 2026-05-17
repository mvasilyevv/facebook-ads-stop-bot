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
const jsdom_1 = require("jsdom");
const registry_js_1 = require("../registry.js");
// Перед регистрацией создаём JSDOM, чтобы шаги, ссылающиеся на document/window,
// импортировались без ошибок (некоторые модули вычисляют DOM-зависимые константы при загрузке).
(0, node_test_1.before)(() => {
    const dom = new jsdom_1.JSDOM('<!doctype html><html><body></body></html>');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.Element = dom.window.Element;
    globalThis.Node = dom.window.Node;
    globalThis.NodeFilter = dom.window.NodeFilter;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.CSS = dom.window.CSS ?? { escape: (s) => s.replace(/"/g, '\\"') };
});
(0, node_test_1.beforeEach)(() => {
    (0, registry_js_1.clearRegistry)();
});
(0, node_test_1.describe)('steps/index', () => {
    (0, node_test_1.it)('регистрирует все 23 шага', async () => {
        await Promise.resolve().then(() => __importStar(require('./index.js')));
        node_assert_1.default.equal((0, registry_js_1.listSteps)().length, 23);
    });
});
//# sourceMappingURL=index.test.js.map