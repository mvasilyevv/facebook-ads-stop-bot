"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const ads_columns_js_1 = require("./ads-columns.js");
function header(surfaceKey, text, left) {
    return { surfaceKey, text, left };
}
function buildFullHeaderSet() {
    return [
        header('toggle', 'Выкл./вкл.', 0),
        header('name', 'Название объявления', 40),
        header('delivery', 'Статус показа', 120),
        header('budget', 'Бюджет', 200),
        header('actions', 'Завершенные регистрации', 280),
        header('actions', 'Лиды', 360),
        header('reach', 'Охват', 440),
        header('impressions', 'Показы', 520),
        header('cost_per_result', 'Цена за результат', 600),
        header('spend', 'Сумма затрат', 680),
        header('clicks', 'Клики', 760),
        header('cpc', 'CPC', 840),
        header('cost_per_action_type', 'Цена за завершенную регистрацию', 920),
        header('cost_per_action_type', 'Цена за лид', 1000),
        header('ctr', 'CTR', 1080),
        header('campaign_group_name', 'Название кампании', 1160),
        header('campaign_name', 'Название группы объявлений', 1240),
        header('outbound_clicks', 'Исходящие клики', 1320),
        header('outbound_clicks_ctr', 'CTR исходящих кликов', 1400),
        header('actions', 'Просмотры целевой страницы', 1480),
        header('cost_per_action_type', 'Цена за просмотр целевой страницы', 1560),
        header('cpm', 'CPM', 1640),
        header('frequency', 'Частота', 1720),
    ];
}
// Сценарий: парсер должен строить layout по фактическому порядку заголовков, а не по захардкоженным индексам.
(0, node_test_1.default)('buildParserColumnLayout учитывает реальный порядок заголовков', () => {
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(buildFullHeaderSet());
    const orderedFields = layout.map((column) => column.fieldName);
    strict_1.default.deepEqual(missingColumns, []);
    strict_1.default.ok(orderedFields.indexOf('registrations') < orderedFields.indexOf('leads'));
    strict_1.default.ok(orderedFields.indexOf('cost_per_registration') < orderedFields.indexOf('cost_per_lead'));
});
// Сценарий: если обязательную колонку переименовали, валидация должна явно пометить её как отсутствующую.
(0, node_test_1.default)('collectMissingValidationColumns находит переименованную обязательную колонку', () => {
    const headers = buildFullHeaderSet().map((item) => (item.surfaceKey === 'actions' && item.text === 'Завершенные регистрации'
        ? header(item.surfaceKey, 'Конверсии', item.left)
        : item));
    const missingColumns = (0, ads_columns_js_1.collectMissingValidationColumns)(headers);
    strict_1.default.ok(missingColumns.includes('Завершенные регистрации'));
    strict_1.default.ok(!missingColumns.includes('Лиды'));
});
// Сценарий: дубли header-нод не должны ломать подсчёт заголовков и смещение ячеек.
(0, node_test_1.default)('normalizeVisibleHeaders удаляет дублирующиеся заголовки', () => {
    const headers = [
        ...buildFullHeaderSet(),
        header('spend', 'Сумма затрат', 680.4),
        header('spend', 'Сумма затрат', 681.1),
    ];
    const normalized = (0, ads_columns_js_1.normalizeVisibleHeaders)(headers);
    const spendHeaders = normalized.filter((item) => item.surfaceKey === 'spend');
    strict_1.default.equal(spendHeaders.length, 1);
});
// Сценарий: нулевая геометрия hidden-header нод не должна ломать распознавание схемы колонок.
(0, node_test_1.default)('buildParserColumnLayout работает для заголовков без видимой геометрии', () => {
    const headers = buildFullHeaderSet().map((item) => ({ ...item, left: 0 }));
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(headers);
    strict_1.default.deepEqual(missingColumns, []);
    strict_1.default.ok(layout.some((column) => column.fieldName === 'ad_name'));
    strict_1.default.ok(layout.some((column) => column.fieldName === 'registrations'));
});
// Сценарий: служебные пустые header-ноды Meta не должны сдвигать индексы реальных колонок.
(0, node_test_1.default)('normalizeVisibleHeaders отбрасывает пустой дубль surfaceKey при наличии текстового заголовка', () => {
    const headers = [
        header('results', 'Результат', 0),
        header('results', '', 0),
        header('reach', 'Охват', 0),
    ];
    const normalized = (0, ads_columns_js_1.normalizeVisibleHeaders)(headers);
    strict_1.default.deepEqual(normalized.map((item) => ({ surfaceKey: item.surfaceKey, text: item.text })), [
        { surfaceKey: 'results', text: 'результат' },
        { surfaceKey: 'reach', text: 'охват' },
    ]);
});
//# sourceMappingURL=ads-columns.test.js.map