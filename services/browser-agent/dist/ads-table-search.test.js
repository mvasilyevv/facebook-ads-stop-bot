"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const ads_table_js_1 = require("./ads-table.js");
// Проверяем, что поиск toggle сначала использует текущий viewport и не делает лишний reset, если строка уже видна.
(0, node_test_1.default)('findToggleCellWithTableScan prefers currently visible row before reset', async () => {
    const cell = { kind: 'cell' };
    const page = {
        $: async (selector) => (selector.includes('table_row:120246283878900334') ? cell : null),
    };
    const found = await (0, ads_table_js_1.findToggleCellWithTableScan)(page, '120246283878900334', {
        resetToTop: true,
    });
    strict_1.default.equal(found, cell);
});
//# sourceMappingURL=ads-table-search.test.js.map