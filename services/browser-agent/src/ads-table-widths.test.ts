import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';

import { buildAdsTableColumnWidthTargets } from './ads-columns.js';
import { applyAdsTableColumnWidthPreset, captureAdsTableColumnWidths } from './ads-table.js';

function cellHtml(content: string, widthPx = 80, separator = false): string {
  const separatorHtml = separator
    ? '<div class="_4lg9" role="separator" style="display:inline-block;width:6px;height:30px;float:right"><div class="_4lga _4lgb"></div></div>'
    : '';
  return `<div class="_4lg0" style="display:inline-block;width:${widthPx}px;height:30px;vertical-align:top">${separatorHtml}${content}</div>`;
}

function resizeHandlerScript(): string {
  return `
    <script>
      let activeResize = null;
      document.addEventListener('mousedown', (event) => {
        const separator = event.target.closest('[role="separator"]');
        if (!separator) return;
        const cell = separator.closest('._4lg0');
        const row = cell && cell.parentElement;
        if (!cell || !row) return;
        const index = Array.from(row.querySelectorAll('._4lg0')).indexOf(cell);
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

// Сценарий: автоширина должна менять видимые строки даже когда Meta отрендерила только часть колонок.
test('applyAdsTableColumnWidthPreset применяет ширины к частично отрендеренной строке', async () => {
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

    assert.equal(result.applied, true);
    assert.deepEqual(result.missingColumns, []);
    assert.deepEqual(bodyWidths, ['49px', '40px', '194px', '110px']);
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
                ? '<div class="_4lg9" role="separator" style="display:inline-block;width:6px;height:30px;float:right"><div class="_4lga _4lgb"></div></div>'
                : '';
              return '<div class="_4lg0" style="display:inline-block;width:' + width + 'px;height:30px;vertical-align:top">' + separatorHtml + content + '</div>';
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
