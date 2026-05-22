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
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const playwright_1 = require("playwright");
const modal_dismisser_js_1 = require("./modal-dismisser.js");
// Вспомогательная функция — запускает headless-браузер и страницу с заданным HTML
async function withPage(html, fn) {
    const browser = await playwright_1.chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.setContent(html);
    try {
        await fn(page);
    }
    finally {
        await browser.close();
    }
}
// Сценарий 1: известный диалог «Сбросить изменения» должен закрываться кнопкой «Отмена»
(0, node_test_1.default)('dismissKnownModals — закрывает диалог reset_changes кнопкой «Отмена»', async () => {
    const html = `
    <html><body>
      <div role="dialog">
        <p>Сбросить изменения в объявлении?</p>
        <button id="btn-reset">Сбросить</button>
        <button id="btn-cancel">Отмена</button>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.equal(result.dismissed.length, 1, 'Ожидается 1 закрытый диалог');
        strict_1.default.equal(result.dismissed[0].id, 'reset_changes');
        strict_1.default.equal(result.unknown.length, 0);
        // Кнопка «Сбросить» не должна быть нажата — проверяем, что диалог закрылся
        // (Playwright скрывает элемент после click-close, либо он остался видимым если closed не сработал)
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 2: неизвестный диалог не закрывается, но артефакт сохранён
(0, node_test_1.default)('dismissKnownModals — неизвестный диалог сохраняет артефакт', async () => {
    const html = `
    <html><body>
      <div role="dialog">
        <p>Какое-то незнакомое сообщение</p>
        <button>OK</button>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.equal(result.dismissed.length, 0, 'Неизвестный диалог не должен быть закрыт');
        strict_1.default.equal(result.unknown.length, 1, 'Должен быть 1 неизвестный артефакт');
        const entry = result.unknown[0];
        strict_1.default.ok(fs.existsSync(entry.htmlPath), `HTML-артефакт должен существовать: ${entry.htmlPath}`);
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 3: запрещённая кнопка «Сбросить» не нажимается — нажата только «Отмена»
(0, node_test_1.default)('dismissKnownModals — не нажимает запрещённую кнопку «Сбросить»', async () => {
    const html = `
    <html><body>
      <div role="dialog">
        <p>Reset changes в объявлении?</p>
        <button id="btn-reset">Reset</button>
        <button id="btn-cancel">Cancel</button>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        // Отслеживаем клики через JS
        await page.evaluate(() => {
            window.__clicked = [];
            document.querySelectorAll('button').forEach((btn) => {
                btn.addEventListener('click', () => {
                    window.__clicked.push(btn.id);
                });
            });
        });
        await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        const clicked = await page.evaluate(() => window.__clicked);
        strict_1.default.ok(!clicked.includes('btn-reset'), 'Кнопка Reset не должна быть нажата');
        strict_1.default.ok(clicked.includes('btn-cancel'), 'Кнопка Cancel должна быть нажата');
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 4: страница без модалок возвращает пустой результат
(0, node_test_1.default)('dismissKnownModals — пустой результат на странице без диалогов', async () => {
    const html = `
    <html><body>
      <p>Просто страница без модальных окон</p>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.equal(result.dismissed.length, 0);
        strict_1.default.equal(result.unknown.length, 0);
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 5: FB jewel-flyout #fbNotificationsFlyout — считается known, артефакты не сохраняются
(0, node_test_1.default)('dismissKnownModals — jewel-flyout #fbNotificationsFlyout не попадает в unknown', async () => {
    const html = `
    <html><body>
      <div id="fbNotificationsFlyout">
        <p>Уведомления Facebook</p>
        <a href="#">Уведомление 1</a>
        <a href="#">Уведомление 2</a>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.ok(result.dismissed.some((d) => d.id === 'fb_notifications_jewel'), 'dismissed должен содержать fb_notifications_jewel');
        strict_1.default.equal(result.unknown.length, 0, 'unknown должен быть пуст — flyout не артефакт');
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 6: пустой uiContextualLayer (служебный wrapper FB) не должен ловиться как unknown.
// Это реальный артефакт из .logs/modals: role="dialog", класс uiContextualLayer, внутри только
// невидимый layer_close_elem. Раньше каждый цикл скана слал TG-алерт «бот не умеет закрывать окно».
(0, node_test_1.default)('dismissKnownModals — пустой uiContextualLayer не попадает в unknown', async () => {
    const html = `
    <html><body>
      <div class="uiContextualLayer uiContextualLayerRight" role="dialog" aria-labelledby="">
        <div class="_5v-0 _53in">
          <div data-non-int-surface="/am/int:_/table/int:Foo.react"></div>
          <a class="accessible_elem layer_close_elem" href="#" role="button" tabindex="0">Закрыть всплывающее окно и продолжить</a>
        </div>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.equal(result.unknown.length, 0, 'uiContextualLayer не должен сохраняться как unknown');
        strict_1.default.equal(result.dismissed.length, 0, 'И не должен попасть в dismissed');
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
// Сценарий 7: скрытый диалог (aria-hidden=true) не считается активным.
(0, node_test_1.default)('dismissKnownModals — aria-hidden диалог не попадает в unknown', async () => {
    const html = `
    <html><body>
      <div role="dialog" aria-hidden="true">
        <p>Закрытый служебный диалог</p>
      </div>
    </body></html>
  `;
    await withPage(html, async (page) => {
        const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
        const result = await (0, modal_dismisser_js_1.dismissKnownModals)(page, { artifactsDir: tmpDir });
        strict_1.default.equal(result.unknown.length, 0, 'aria-hidden диалог пропускается');
        fs.rmSync(tmpDir, { recursive: true, force: true });
    });
});
//# sourceMappingURL=modal-dismisser.test.js.map