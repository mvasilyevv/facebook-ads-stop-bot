"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.addCreatorEventListener = addCreatorEventListener;
exports.injectCreator = injectCreator;
// Подгрузка creator-бандла и установка fbAgentEmit-биндинга в каждый контекст.
// Бандл собирается esbuild'ом в dist/creator-bundle.js (см. package.json bundle:creator).
const node_fs_1 = __importDefault(require("node:fs"));
const node_path_1 = __importDefault(require("node:path"));
const BUNDLE_PATH = node_path_1.default.resolve(__dirname, 'creator-bundle.js');
let cachedBundle = null;
function loadBundle() {
    if (cachedBundle)
        return cachedBundle;
    if (!node_fs_1.default.existsSync(BUNDLE_PATH)) {
        throw new Error(`creator-bundle.js не найден по пути ${BUNDLE_PATH}. Запустите npm run bundle:creator`);
    }
    cachedBundle = node_fs_1.default.readFileSync(BUNDLE_PATH, 'utf8');
    return cachedBundle;
}
const contextListeners = new WeakMap();
function addCreatorEventListener(context, listener) {
    let bag = contextListeners.get(context);
    if (!bag) {
        bag = new Set();
        contextListeners.set(context, bag);
    }
    bag.add(listener);
    return () => bag?.delete(listener);
}
function getListeners(context) {
    return contextListeners.get(context) ?? new Set();
}
// Инжект бандла + биндинга. Идемпотентен по контексту: повторный вызов — no-op.
const injectedContexts = new WeakSet();
async function injectCreator(context) {
    if (injectedContexts.has(context))
        return;
    // Защита от моков: в тестах контекст может не иметь exposeBinding/addInitScript.
    if (typeof context.exposeBinding !== 'function' ||
        typeof context.addInitScript !== 'function') {
        return;
    }
    injectedContexts.add(context);
    // exposeBinding регистрирует window.fbAgentEmit, доступный в каждой новой странице.
    await context.exposeBinding('fbAgentEmit', (_source, event, payload) => {
        for (const listener of getListeners(context)) {
            try {
                listener(event, payload);
            }
            catch {
                // Слушатели изолированы — не валим инжект из-за ошибки одного.
            }
        }
    });
    // Бандл устанавливает window.__fbAgent в каждом документе до запуска скриптов страницы.
    await context.addInitScript({ content: loadBundle() });
}
//# sourceMappingURL=creator-injector.js.map