import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { chromium } from 'playwright';
import { dismissKnownModals } from './modal-dismisser.js';

// Вспомогательная функция — запускает headless-браузер и страницу с заданным HTML
async function withPage(html: string, fn: (page: any) => Promise<void>): Promise<void> {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setContent(html);
  try {
    await fn(page);
  } finally {
    await browser.close();
  }
}

// Сценарий 1: известный диалог «Сбросить изменения» должен закрываться кнопкой «Отмена»
test('dismissKnownModals — закрывает диалог reset_changes кнопкой «Отмена»', async () => {
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
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.equal(result.dismissed.length, 1, 'Ожидается 1 закрытый диалог');
    assert.equal(result.dismissed[0].id, 'reset_changes');
    assert.equal(result.unknown.length, 0);

    // Кнопка «Сбросить» не должна быть нажата — проверяем, что диалог закрылся
    // (Playwright скрывает элемент после click-close, либо он остался видимым если closed не сработал)
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 2: неизвестный диалог не закрывается, но артефакт сохранён
test('dismissKnownModals — неизвестный диалог сохраняет артефакт', async () => {
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
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.equal(result.dismissed.length, 0, 'Неизвестный диалог не должен быть закрыт');
    assert.equal(result.unknown.length, 1, 'Должен быть 1 неизвестный артефакт');

    const entry = result.unknown[0];
    assert.ok(fs.existsSync(entry.htmlPath), `HTML-артефакт должен существовать: ${entry.htmlPath}`);

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 3: запрещённая кнопка «Сбросить» не нажимается — нажата только «Отмена»
test('dismissKnownModals — не нажимает запрещённую кнопку «Сбросить»', async () => {
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
      (window as any).__clicked = [];
      document.querySelectorAll('button').forEach((btn) => {
        btn.addEventListener('click', () => {
          (window as any).__clicked.push((btn as HTMLButtonElement).id);
        });
      });
    });

    await dismissKnownModals(page, { artifactsDir: tmpDir });

    const clicked: string[] = await page.evaluate(() => (window as any).__clicked);
    assert.ok(!clicked.includes('btn-reset'), 'Кнопка Reset не должна быть нажата');
    assert.ok(clicked.includes('btn-cancel'), 'Кнопка Cancel должна быть нажата');

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 4: страница без модалок возвращает пустой результат
test('dismissKnownModals — пустой результат на странице без диалогов', async () => {
  const html = `
    <html><body>
      <p>Просто страница без модальных окон</p>
    </body></html>
  `;

  await withPage(html, async (page) => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.equal(result.dismissed.length, 0);
    assert.equal(result.unknown.length, 0);

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 5: FB jewel-flyout #fbNotificationsFlyout — считается known, артефакты не сохраняются
test('dismissKnownModals — jewel-flyout #fbNotificationsFlyout не попадает в unknown', async () => {
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
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.ok(
      result.dismissed.some((d) => d.id === 'fb_notifications_jewel'),
      'dismissed должен содержать fb_notifications_jewel',
    );
    assert.equal(result.unknown.length, 0, 'unknown должен быть пуст — flyout не артефакт');

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 6: пустой uiContextualLayer (служебный wrapper FB) не должен ловиться как unknown.
// Это реальный артефакт из .logs/modals: role="dialog", класс uiContextualLayer, внутри только
// невидимый layer_close_elem. Раньше каждый цикл скана слал TG-алерт «бот не умеет закрывать окно».
test('dismissKnownModals — пустой uiContextualLayer не попадает в unknown', async () => {
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
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.equal(result.unknown.length, 0, 'uiContextualLayer не должен сохраняться как unknown');
    assert.equal(result.dismissed.length, 0, 'И не должен попасть в dismissed');

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});

// Сценарий 7: скрытый диалог (aria-hidden=true) не считается активным.
test('dismissKnownModals — aria-hidden диалог не попадает в unknown', async () => {
  const html = `
    <html><body>
      <div role="dialog" aria-hidden="true">
        <p>Закрытый служебный диалог</p>
      </div>
    </body></html>
  `;

  await withPage(html, async (page) => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'modal-test-'));
    const result = await dismissKnownModals(page, { artifactsDir: tmpDir });

    assert.equal(result.unknown.length, 0, 'aria-hidden диалог пропускается');

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});
