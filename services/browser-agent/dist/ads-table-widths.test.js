"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const playwright_1 = require("playwright");
const ads_columns_js_1 = require("./ads-columns.js");
const ads_table_js_1 = require("./ads-table.js");
function cellHtml(content) {
    return `<div class="_4lg0" style="display:inline-block;width:80px;height:30px;vertical-align:top">${content}</div>`;
}
// Сценарий: автоширина должна менять видимые строки даже когда Meta отрендерила только часть колонок.
(0, node_test_1.default)('applyAdsTableColumnWidthPreset применяет ширины к частично отрендеренной строке', async () => {
    const browser = await playwright_1.chromium.launch({ headless: true });
    const page = await browser.newPage();
    try {
        const targets = (0, ads_columns_js_1.buildAdsTableColumnWidthTargets)();
        const headerCells = targets.slice(0, 3).map((target) => cellHtml(`<div role="columnheader" style="width:80px;height:30px">
        <span data-surface="table_column_header:${target.surfaceKey}">${target.title}</span>
      </div>`));
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
        const result = await (0, ads_table_js_1.applyAdsTableColumnWidthPreset)(page);
        const bodyWidths = await page.$$eval('#body-row ._4lg0', (nodes) => (nodes.map((node) => node.style.width)));
        strict_1.default.equal(result.applied, true);
        strict_1.default.deepEqual(result.missingColumns, []);
        strict_1.default.deepEqual(bodyWidths, ['49px', '40px', '194px', '110px']);
    }
    finally {
        await browser.close();
    }
});
// Сценарий: автоширина проходит горизонтальный viewport слева направо и находит правые колонки.
(0, node_test_1.default)('applyAdsTableColumnWidthPreset прокручивает таблицу вправо для скрытых колонок', async () => {
    const browser = await playwright_1.chromium.launch({ headless: true });
    const page = await browser.newPage();
    try {
        const targets = (0, ads_columns_js_1.buildAdsTableColumnWidthTargets)();
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

            function cell(content) {
              return '<div class="_4lg0" style="display:inline-block;width:80px;height:30px;vertical-align:top">' + content + '</div>';
            }

            function render() {
              const start = Math.min(targets.length - 3, Math.floor(scroller.scrollLeft / 140));
              const visible = targets.slice(start, start + 3);
              const offset = scroller.scrollLeft;
              header.style.transform = 'translateX(' + offset + 'px)';
              bodyRow.style.transform = 'translateX(' + offset + 'px)';
              header.innerHTML = cell('selection') + visible.map((target) => cell(
                '<div role="columnheader" style="width:80px;height:30px">' +
                  '<span data-surface="table_column_header:' + target.surfaceKey + '">' + target.title + '</span>' +
                '</div>'
              )).join('');
              bodyRow.innerHTML = cell('selection') + visible.map((target) => cell(target.key)).join('');
            }

            scroller.addEventListener('scroll', render);
            render();
          </script>
        </body>
      </html>
    `);
        const result = await (0, ads_table_js_1.applyAdsTableColumnWidthPreset)(page);
        const scrollLeft = await page.$eval('#scroller', (node) => node.scrollLeft);
        strict_1.default.equal(result.applied, true);
        strict_1.default.ok(result.matchedColumns.includes('Название кампании'));
        strict_1.default.ok(result.matchedColumns.includes('Название группы объявлений'));
        strict_1.default.equal(scrollLeft, 0);
    }
    finally {
        await browser.close();
    }
});
//# sourceMappingURL=ads-table-widths.test.js.map