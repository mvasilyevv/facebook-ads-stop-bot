"use strict";
// Юнит-тесты детектора причины пустого скана.
// Сценарии: таблицы нет, таблица есть но фильтр исключает всё, таблица есть но активных кампаний нет.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const empty_reason_js_1 = require("./empty-reason.js");
(0, node_test_1.default)('detectEmptyReason: возвращает table_not_found, когда нет хедера таблицы', () => {
    strict_1.default.equal((0, empty_reason_js_1.detectEmptyReason)({ hasTableHeader: false, hasFilterChips: false, rowCount: 0 }), 'table_not_found');
});
(0, node_test_1.default)('detectEmptyReason: возвращает filter_excludes_all, когда хедер есть, есть фильтр-чипы и 0 строк', () => {
    strict_1.default.equal((0, empty_reason_js_1.detectEmptyReason)({ hasTableHeader: true, hasFilterChips: true, rowCount: 0 }), 'filter_excludes_all');
});
(0, node_test_1.default)('detectEmptyReason: возвращает no_active_ads, когда хедер есть, фильтров нет и 0 строк', () => {
    strict_1.default.equal((0, empty_reason_js_1.detectEmptyReason)({ hasTableHeader: true, hasFilterChips: false, rowCount: 0 }), 'no_active_ads');
});
(0, node_test_1.default)('detectEmptyReason: возвращает null, когда есть хотя бы одна строка', () => {
    strict_1.default.equal((0, empty_reason_js_1.detectEmptyReason)({ hasTableHeader: true, hasFilterChips: false, rowCount: 1 }), null);
});
//# sourceMappingURL=empty-reason.test.js.map