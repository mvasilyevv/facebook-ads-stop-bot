import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';

import { buildAdsTableColumnWidthTargets } from './ads-columns.js';
import { applyAdsTableColumnWidthPreset } from './ads-table.js';

function cellHtml(content: string): string {
  return `<div class="_4lg0" style="display:inline-block;width:80px;height:30px;vertical-align:top">${content}</div>`;
}

// Сценарий: автоширина должна менять видимые строки даже когда Meta отрендерила только часть колонок.
test('applyAdsTableColumnWidthPreset применяет ширины к частично отрендеренной строке', async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    const targets = buildAdsTableColumnWidthTargets();
    const headerCells = targets.slice(0, 3).map((target) => cellHtml(
      `<div role="columnheader" style="width:80px;height:30px">
        <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
      </div>`,
    ));
    const partialBodyCells = [
      cellHtml('selection'),
      cellHtml('toggle'),
      cellHtml('name'),
      cellHtml('delivery'),
    ].join('');

    await page.setContent(`
      <html>
        <body>
          <div role="row" id="header">${headerCells.join('')}</div>
          <div class="_1gda _2djg" id="body-row">${partialBodyCells}</div>
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
