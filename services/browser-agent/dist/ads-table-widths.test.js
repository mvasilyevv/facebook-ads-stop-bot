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
        const headerCells = targets.map((target) => cellHtml(`<div role="columnheader" style="width:80px;height:30px">
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
        strict_1.default.deepEqual(bodyWidths, ['49px', '40px', '194px', '110px']);
    }
    finally {
        await browser.close();
    }
});
//# sourceMappingURL=ads-table-widths.test.js.map