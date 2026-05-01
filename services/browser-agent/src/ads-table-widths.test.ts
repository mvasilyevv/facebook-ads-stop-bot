import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';

import { buildAdsTableColumnWidthTargets } from './ads-columns.js';
import { applyAdsTableColumnWidthPreset, captureAdsTableColumnWidths } from './ads-table.js';

function cellHtml(content: string, widthPx = 80, separator = false): string {
  const separatorHtml = separator
    ? '<div class="_4lg9" role="separator" style="position:absolute;right:0;top:0;width:6px;height:30px;z-index:2"><div class="_4lga _4lgb"></div></div>'
    : '';
  return `<div class="_4lg0" style="position:relative;display:inline-block;width:${widthPx}px;height:30px;vertical-align:top">${content}${separatorHtml}</div>`;
}

function resizeHandlerScript(): string {
  return `
    <script>
      let activeResize = null;
      window.__resizeStarts = 0;
      document.addEventListener('mousedown', (event) => {
        const separator = event.target.closest('[role="separator"]');
        if (!separator) return;
        const cell = separator.closest('._4lg0');
        const row = cell && cell.parentElement;
        if (!cell || !row) return;
        const index = Array.from(row.querySelectorAll('._4lg0')).indexOf(cell);
        window.__resizeStarts += 1;
        activeResize = { index, startX: event.clientX, startWidth: cell.getBoundingClientRect().width };
        event.preventDefault();
      });
      document.addEventListener('mousemove', (event) => {
        if (!activeResize) return;
        const width = Math.max(30, Math.round(activeResize.startWidth + event.clientX - activeResize.startX));
        for (const row of document.querySelectorAll('[role="row"], ._1gda._2djg')) {
          const cell = row.querySelectorAll('._4lg0')[activeResize.index];
          if (cell) cell.style.width = width + 'px';
        }
      });
      document.addEventListener('mouseup', () => {
        activeResize = null;
      });
    </script>
  `;
}

// Сценарий: автоширина не должна выдавать полный успех, если отрендерена только часть обязательных колонок.
test('applyAdsTableColumnWidthPreset сообщает о пропущенных колонках при частичном рендере', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets();
    const headerCells = [
      cellHtml('selection', 49),
      ...targets.slice(0, 3).map((target) => cellHtml(
        `<div role="columnheader" style="width:80px;height:30px">
          <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
        </div>`,
        80,
        true,
      )),
    ];
    const partialBodyCells = [
      cellHtml('selection', 49),
      cellHtml('toggle'),
      cellHtml('name'),
      cellHtml('delivery'),
    ].join('');

    await page.setContent(`
      <html>
        <body>
          <div role="row" id="header">${headerCells.join('')}</div>
          <div class="_1gda _2djg" id="body-row">${partialBodyCells}</div>
          ${resizeHandlerScript()}
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page);
    const bodyWidths = await page.$$eval('#body-row ._4lg0', (nodes) => (
      nodes.map((node) => (node as HTMLElement).style.width)
    ));

    assert.equal(result.applied, false);
    assert.ok(result.missingColumns.includes('Бюджет'));
    assert.ok(result.errorMessage.includes('Не обработаны колонки автоширины'));
    assert.deepEqual(bodyWidths, ['49px', '40px', '194px', '110px']);
  } finally {
    await browser.close();
  }
});

// Сценарий: автоширина пропускает колонки, которые уже имеют нужную ширину.
test('applyAdsTableColumnWidthPreset не тянет разделитель для колонок с нужной шириной', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets().slice(0, 3);
    const headerCells = [
      ...targets.map((target) => cellHtml(
        `<div role="columnheader" style="width:${target.widthPx}px;height:30px">
          <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
        </div>`,
        target.widthPx,
        true,
      )),
    ];
    const bodyCells = targets.map((target) => cellHtml(target.key, target.widthPx)).join('');

    await page.setContent(`
      <html>
        <body>
          <div role="row" id="header">${headerCells.join('')}</div>
          <div class="_1gda _2djg" id="body-row">${bodyCells}</div>
          ${resizeHandlerScript()}
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page, targets);
    const resizeStarts = await page.evaluate(() => (window as any).__resizeStarts);

    assert.equal(result.applied, true);
    assert.equal(result.adjustedCells, 0);
    assert.equal(resizeStarts, 0);
    assert.deepEqual(result.missingColumns, []);
    assert.deepEqual(result.matchedColumns, targets.map((target) => target.title));
  } finally {
    await browser.close();
  }
});

// Сценарий: перед drag автоширина закрывает подсказку, которая перекрывает separator.
test('applyAdsTableColumnWidthPreset закрывает tooltip перед перетягиванием separator', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets().slice(0, 1);
    const target = targets[0];

    await page.setContent(`
      <html>
        <body>
          <div role="row" id="header">
            ${cellHtml(
              `<div role="columnheader" style="width:80px;height:30px">
                <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
              </div>`,
              80,
              true,
            )}
          </div>
          <div class="_1gda _2djg" id="body-row">${cellHtml(target.key, 80)}</div>
          <div id="tooltip" style="position:fixed;left:0;top:0;right:0;bottom:0;z-index:10000;background:transparent"></div>
          ${resizeHandlerScript()}
          <script>
            document.addEventListener('keydown', (event) => {
              if (event.key === 'Escape') document.getElementById('tooltip')?.remove();
            });
          </script>
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page, targets);
    const bodyWidth = await page.$eval('#body-row ._4lg0', (node) => (
      Math.round((node as HTMLElement).getBoundingClientRect().width)
    ));
    const tooltipExists = await page.$('#tooltip');

    assert.equal(result.applied, true);
    assert.equal(bodyWidth, target.widthPx);
    assert.equal(tooltipExists, null);
  } finally {
    await browser.close();
  }
});

// Сценарий: сохранение слепка читает фактические ширины текущих заголовков Ads Manager.
test('captureAdsTableColumnWidths сохраняет текущие ширины колонок', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets();
    const headerCells = [
      cellHtml('selection', 49),
      ...targets.slice(0, 2).map((target, index) => cellHtml(
        `<div role="columnheader" style="width:80px;height:30px">
          <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
        </div>`,
        index === 0 ? 123 : 234,
        true,
      )),
      cellHtml(
        `<div role="columnheader" style="width:80px;height:30px">
          <span data-surface="table_column_header:bid_strategy">Стратегия ставок</span>
        </div>`,
        345,
        true,
      ),
    ];

    await page.setContent(`
      <html>
        <body>
          <div role="row" id="header">${headerCells.join('')}</div>
        </body>
      </html>
    `);

    const result = await captureAdsTableColumnWidths(page);
    const widthsByKey = new Map(result.columnWidths.map((column) => [column.key, column.widthPx]));

    assert.equal(result.captured, true);
    assert.equal(widthsByKey.get(targets[0].key), 123);
    assert.equal(widthsByKey.get(targets[1].key), 234);
    assert.equal(
      result.columnWidths.find((column) => column.title === 'Стратегия ставок')?.widthPx,
      345,
    );
  } finally {
    await browser.close();
  }
});

// Сценарий: автоширина проходит горизонтальный viewport слева направо и находит правые колонки.
test('applyAdsTableColumnWidthPreset прокручивает таблицу вправо для скрытых колонок', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets();
    const browserTargets = targets.map((target) => ({
      key: target.key,
      title: target.title,
      surfaceKey: target.surfaceKey,
    }));

    await page.setContent(`
      <html>
        <body>
          <div id="scroller" style="width:320px;height:120px;overflow-x:auto;overflow-y:hidden">
            <div id="wide" style="width:${targets.length * 180}px;height:90px">
              <div id="header" role="row" style="height:34px;white-space:nowrap"></div>
              <div id="body-row" class="_1gda _2djg" style="height:34px;white-space:nowrap"></div>
            </div>
          </div>
          <script>
            const targets = ${JSON.stringify(browserTargets)};
            const scroller = document.getElementById('scroller');
            const header = document.getElementById('header');
            const bodyRow = document.getElementById('body-row');

            const widths = Object.fromEntries(targets.map((target) => [target.key, 80]));

            function cell(content, width, separator) {
              const separatorHtml = separator
                ? '<div class="_4lg9" role="separator" style="position:absolute;right:0;top:0;width:6px;height:30px;z-index:2"><div class="_4lga _4lgb"></div></div>'
                : '';
              return '<div class="_4lg0" style="position:relative;display:inline-block;width:' + width + 'px;height:30px;vertical-align:top">' + content + separatorHtml + '</div>';
            }

            function render() {
              const start = Math.min(targets.length - 3, Math.floor(scroller.scrollLeft / 140));
              const visible = targets.slice(start, start + 3);
              const offset = scroller.scrollLeft;
              header.style.transform = 'translateX(' + offset + 'px)';
              bodyRow.style.transform = 'translateX(' + offset + 'px)';
              header.innerHTML = cell('selection', 49, false) + visible.map((target) => cell(
                '<div role="columnheader" style="width:80px;height:30px">' +
                  '<span data-surface="table_column_header:' + target.surfaceKey + '">' + target.title + '</span>' +
                '</div>',
                widths[target.key],
                true
              )).join('');
              bodyRow.innerHTML = cell('selection', 49, false) + visible.map((target) => cell(target.key, widths[target.key], false)).join('');
            }

            scroller.addEventListener('scroll', render);
            render();
            let activeResize = null;
            document.addEventListener('mousedown', (event) => {
              const separator = event.target.closest('[role="separator"]');
              if (!separator) return;
              const cell = separator.closest('._4lg0');
              const row = cell && cell.parentElement;
              const cells = row ? Array.from(row.querySelectorAll('._4lg0')) : [];
              const index = cells.indexOf(cell);
              const visible = targets.slice(Math.min(targets.length - 3, Math.floor(scroller.scrollLeft / 140)), Math.min(targets.length - 3, Math.floor(scroller.scrollLeft / 140)) + 3);
              const target = visible[index - 1];
              if (!target) return;
              activeResize = { key: target.key, startX: event.clientX, startWidth: cell.getBoundingClientRect().width };
              event.preventDefault();
            });
            document.addEventListener('mousemove', (event) => {
              if (!activeResize) return;
              widths[activeResize.key] = Math.max(30, Math.round(activeResize.startWidth + event.clientX - activeResize.startX));
              render();
            });
            document.addEventListener('mouseup', () => {
              activeResize = null;
            });
          </script>
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page);
    const scrollLeft = await page.$eval('#scroller', (node) => (node as HTMLElement).scrollLeft);

    assert.equal(result.applied, true);
    assert.ok(result.matchedColumns.includes('Название кампании'));
    assert.ok(result.matchedColumns.includes('Название группы объявлений'));
    assert.equal(scrollLeft, 0);
  } finally {
    await browser.close();
  }
});

// Сценарий: автоширина не засчитывает offscreen-разделители и сначала прокручивает контейнер.
test('applyAdsTableColumnWidthPreset не считает offscreen-разделители обработанными', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  try {
    const targets = buildAdsTableColumnWidthTargets().slice(0, 8);
    const headerCells = targets.map((target) => cellHtml(
      `<div role="columnheader" style="width:80px;height:30px">
        <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
      </div>`,
      160,
      true,
    ));
    const bodyCells = targets.map((target) => cellHtml(target.key, 160)).join('');

    await page.setContent(`
      <html>
        <body>
          <div id="scroller" style="width:360px;height:120px;overflow-x:hidden;overflow-y:hidden">
            <div id="wide" style="width:${targets.length * 160}px;height:90px">
              <div role="row" id="header" style="height:34px;white-space:nowrap">${headerCells.join('')}</div>
              <div class="_1gda _2djg" id="body-row" style="height:34px;white-space:nowrap">${bodyCells}</div>
            </div>
          </div>
          ${resizeHandlerScript()}
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page, targets);
    const bodyWidths = await page.$$eval('#body-row ._4lg0', (nodes) => (
      nodes.map((node) => Math.round((node as HTMLElement).getBoundingClientRect().width))
    ));
    const expectedWidths = targets.map((target) => target.widthPx);
    const scrollLeft = await page.$eval('#scroller', (node) => (node as HTMLElement).scrollLeft);

    assert.equal(result.applied, true);
    assert.deepEqual(result.missingColumns, []);
    assert.deepEqual(bodyWidths, expectedWidths);
    assert.equal(scrollLeft, 0);
  } finally {
    await browser.close();
  }
});

// Сценарий: если следующий заголовок виден без разделителя, автоширина делает короткий скролл вправо.
test('applyAdsTableColumnWidthPreset коротко скроллит вправо к недоступному разделителю', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets().slice(0, 3);
    const browserTargets = targets.map((target) => ({
      key: target.key,
      title: target.title,
      surfaceKey: target.surfaceKey,
    }));

    await page.setContent(`
      <html>
        <body>
          <div id="scroller" style="width:320px;height:120px;overflow-x:auto;overflow-y:hidden">
            <div id="wide" style="width:420px;height:90px">
              <div id="header" role="row" style="height:34px;white-space:nowrap"></div>
              <div id="body-row" class="_1gda _2djg" style="height:34px;white-space:nowrap"></div>
            </div>
          </div>
          <script>
            const targets = ${JSON.stringify(browserTargets)};
            const scroller = document.getElementById('scroller');
            const header = document.getElementById('header');
            const bodyRow = document.getElementById('body-row');

            const widths = Object.fromEntries(targets.map((target) => [target.key, 80]));
            window.__scrollPositions = [];

            function cell(content, width, separator) {
              const separatorHtml = separator
                ? '<div class="_4lg9" role="separator" style="position:absolute;right:0;top:0;width:6px;height:30px;z-index:2"><div class="_4lga _4lgb"></div></div>'
                : '';
              return '<div class="_4lg0" style="position:relative;display:inline-block;width:' + width + 'px;height:30px;vertical-align:top">' + content + separatorHtml + '</div>';
            }

            function render() {
              const scrollLeft = scroller.scrollLeft;
              header.innerHTML = targets.map((target, index) => {
                const separatorVisible = index < 2 || (scrollLeft >= 50 && scrollLeft <= 90);
                return cell(
                  '<div role="columnheader" style="width:80px;height:30px">' +
                    '<span data-surface="table_column_header:' + target.surfaceKey + '">' + target.title + '</span>' +
                  '</div>',
                  widths[target.key],
                  separatorVisible
                );
              }).join('');
              bodyRow.innerHTML = targets.map((target) => cell(target.key, widths[target.key], false)).join('');
            }

            scroller.addEventListener('scroll', () => {
              window.__scrollPositions.push(scroller.scrollLeft);
              render();
            });
            render();
            let activeResize = null;
            document.addEventListener('mousedown', (event) => {
              const separator = event.target.closest('[role="separator"]');
              if (!separator) return;
              const cellNode = separator.closest('._4lg0');
              const row = cellNode && cellNode.parentElement;
              const cells = row ? Array.from(row.querySelectorAll('._4lg0')) : [];
              const index = cells.indexOf(cellNode);
              const target = targets[index];
              if (!target) return;
              activeResize = { key: target.key, startX: event.clientX, startWidth: cellNode.getBoundingClientRect().width };
              event.preventDefault();
            });
            document.addEventListener('mousemove', (event) => {
              if (!activeResize) return;
              widths[activeResize.key] = Math.max(30, Math.round(activeResize.startWidth + event.clientX - activeResize.startX));
              render();
            });
            document.addEventListener('mouseup', () => {
              activeResize = null;
            });
          </script>
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page, targets);
    const scrollPositions = await page.evaluate(() => (window as any).__scrollPositions);

    assert.equal(result.applied, true);
    assert.ok(result.matchedColumns.includes(targets[2].title));
    assert.ok(scrollPositions.includes(80));
  } finally {
    await browser.close();
  }
});

// Сценарий: широкая колонка с началом слева от viewport сжимается в несколько drag-проходов.
test('applyAdsTableColumnWidthPreset дожимает широкие колонки после обратного скролла', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  try {
    const targets = buildAdsTableColumnWidthTargets()
      .filter((target) => ['campaign_name', 'adset_name'].includes(target.key));
    const browserTargets = targets.map((target) => ({
      key: target.key,
      title: target.title,
      surfaceKey: target.surfaceKey,
      widthPx: target.widthPx,
    }));

    await page.setContent(`
      <html>
        <body>
          <div id="scroller" style="width:320px;height:120px;overflow-x:auto;overflow-y:hidden">
            <div id="wide" style="width:1100px;height:90px">
              <div id="header" role="row" style="height:34px;white-space:nowrap"></div>
              <div id="body-row" class="_1gda _2djg" style="height:34px;white-space:nowrap"></div>
            </div>
          </div>
          <script>
            const targets = ${JSON.stringify(browserTargets)};
            const scroller = document.getElementById('scroller');
            const header = document.getElementById('header');
            const bodyRow = document.getElementById('body-row');
            const widths = Object.fromEntries(targets.map((target) => [target.key, 520]));
            window.__scrollPositions = [];
            window.__widths = widths;

            function cell(content, width, separator) {
              const separatorHtml = separator
                ? '<div class="_4lg9" role="separator" style="position:absolute;right:0;top:0;width:8px;height:30px"><div class="_4lga _4lgb"></div></div>'
                : '';
              return '<div class="_4lg0" style="position:relative;display:inline-block;width:' + width + 'px;height:30px;vertical-align:top">' + content + separatorHtml + '</div>';
            }

            function render() {
              header.innerHTML = targets.map((target) => cell(
                '<div role="columnheader" style="width:' + widths[target.key] + 'px;height:30px">' +
                  '<span data-surface="table_column_header:' + target.surfaceKey + '">' + target.title + '</span>' +
                '</div>',
                widths[target.key],
                true
              )).join('');
              bodyRow.innerHTML = targets.map((target) => cell(target.key, widths[target.key], false)).join('');
            }

            scroller.addEventListener('scroll', () => {
              window.__scrollPositions.push(scroller.scrollLeft);
            });
            render();

            let activeResize = null;
            document.addEventListener('mousedown', (event) => {
              const separator = event.target.closest('[role="separator"]');
              if (!separator) return;
              const cellNode = separator.closest('._4lg0');
              const row = cellNode && cellNode.parentElement;
              const cells = row ? Array.from(row.querySelectorAll('._4lg0')) : [];
              const index = cells.indexOf(cellNode);
              const target = targets[index];
              if (!target) return;
              activeResize = { key: target.key, startX: event.clientX, startWidth: cellNode.getBoundingClientRect().width };
              event.preventDefault();
            });
            document.addEventListener('mousemove', (event) => {
              if (!activeResize) return;
              const scrollerRect = scroller.getBoundingClientRect();
              if (event.clientX < scrollerRect.left + 8 || event.clientX > scrollerRect.right - 8) return;
              widths[activeResize.key] = Math.max(30, Math.round(activeResize.startWidth + event.clientX - activeResize.startX));
              render();
            });
            document.addEventListener('mouseup', () => {
              activeResize = null;
            });
          </script>
        </body>
      </html>
    `);

    const result = await applyAdsTableColumnWidthPreset(page, targets);
    const widths = await page.evaluate(() => ({ ...(window as any).__widths }));
    const scrollPositions = await page.evaluate(() => (window as any).__scrollPositions);

    assert.equal(result.applied, true);
    assert.deepEqual(result.missingColumns, []);
    assert.equal(result.adjustedCells, 2);
    assert.equal(widths.campaign_name, 40);
    assert.equal(widths.adset_name, 40);
    assert.ok(scrollPositions.some((position: number) => position > 0));
    assert.ok(scrollPositions.includes(0));
  } finally {
    await browser.close();
  }
});
