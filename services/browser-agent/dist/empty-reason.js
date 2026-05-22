"use strict";
// Чистая функция: по фактам о DOM Ads Manager решает причину пустого скана.
// Факты собирает caller через page.evaluate — есть ли хедер таблицы и видны ли чипы фильтра.
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectEmptyReason = detectEmptyReason;
function detectEmptyReason(input) {
    if (input.rowCount > 0) {
        return null;
    }
    if (!input.hasTableHeader) {
        return 'table_not_found';
    }
    if (input.hasFilterChips) {
        return 'filter_excludes_all';
    }
    return 'no_active_ads';
}
//# sourceMappingURL=empty-reason.js.map