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
        header('results', 'Результат', 280),
        header('reach', 'Охват', 360),
        header('impressions', 'Показы', 440),
        header('cost_per_result', 'Цена за результат', 520),
        header('spend', 'Сумма затрат', 600),
        header('clicks', 'Клики', 680),
        header('cpc', 'CPC', 760),
        header('actions', 'Лиды', 840),
        header('cost_per_action_type', 'Цена за лид', 920),
        header('actions', 'Завершенные регистрации', 1000),
        header('cost_per_action_type', 'Цена за завершенную регистрацию', 1080),
        header('ctr', 'CTR', 1160),
        header('campaign_group_name', 'Название кампании', 1240),
        header('campaign_name', 'Название группы объявлений', 1320),
        header('outbound_clicks', 'Исходящие клики', 1400),
        header('outbound_clicks_ctr', 'CTR исходящих кликов', 1480),
        header('actions', 'Просмотры целевой страницы', 1560),
        header('cost_per_action_type', 'Цена за просмотр целевой страницы', 1640),
        header('cpm', 'CPM', 1720),
        header('frequency', 'Частота', 1800),
    ];
}
// Сценарий: парсер должен строить layout по фактическому порядку заголовков, а не по захардкоженным индексам.
(0, node_test_1.default)('buildParserColumnLayout учитывает реальный порядок заголовков', () => {
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(buildFullHeaderSet());
    const orderedFields = layout.map((column) => column.fieldName);
    strict_1.default.deepEqual(missingColumns, []);
    strict_1.default.ok(orderedFields.indexOf('deposits') < orderedFields.indexOf('cost_per_result'));
    strict_1.default.ok(orderedFields.indexOf('leads') < orderedFields.indexOf('registrations'));
    strict_1.default.ok(orderedFields.indexOf('cost_per_lead') < orderedFields.indexOf('cost_per_registration'));
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
// Сценарий: диагностические и ранние traffic-колонки можно скрыть без поломки обязательного парсинга.
(0, node_test_1.default)('buildParserColumnLayout не требует необязательные traffic-колонки', () => {
    const headers = buildFullHeaderSet().filter((item) => ![
        'outbound_clicks',
        'outbound_clicks_ctr',
        'cpm',
        'frequency',
    ].includes(item.surfaceKey));
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(headers);
    const fields = layout.map((column) => column.fieldName);
    strict_1.default.deepEqual(missingColumns, []);
    strict_1.default.ok(fields.includes('deposits'));
    strict_1.default.ok(!fields.includes('outbound_clicks'));
});
// Сценарий: колонка результата обязательна, потому что в текущем Ads Manager она означает депозиты.
(0, node_test_1.default)('collectMissingValidationColumns требует колонку результата для депозитов', () => {
    const headers = buildFullHeaderSet().filter((item) => item.surfaceKey !== 'results');
    const missingColumns = (0, ads_columns_js_1.collectMissingValidationColumns)(headers);
    strict_1.default.ok(missingColumns.includes('Результат'));
});
// Сценарий: дубли header-нод не должны ломать подсчёт заголовков и смещение ячеек.
(0, node_test_1.default)('normalizeVisibleHeaders удаляет дублирующиеся заголовки', () => {
    const headers = [
        ...buildFullHeaderSet(),
        header('spend', 'Сумма затрат', 600.4),
        header('spend', 'Сумма затрат', 601.1),
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
// Сценарий: колонки кампании и группы объявлений распознаются по тексту, если Meta поменяла data-surface.
(0, node_test_1.default)('buildParserColumnLayout распознаёт campaign/adset при изменившихся surfaceKey', () => {
    const headers = buildFullHeaderSet().map((item) => {
        if (item.surfaceKey === 'campaign_group_name') {
            return header('campaign_name', 'Название кампании', item.left);
        }
        if (item.surfaceKey === 'campaign_name') {
            return header('adset_name', 'Название группы объявлений', item.left);
        }
        return item;
    });
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(headers);
    const fields = layout.map((column) => column.fieldName);
    strict_1.default.deepEqual(missingColumns, []);
    strict_1.default.ok(fields.includes('campaign_name'));
    strict_1.default.ok(fields.includes('adset_name'));
});
// Сценарий: пресет автоширины должен повторять текущую ручную раскладку Ads Manager один в один.
(0, node_test_1.default)('buildAdsTableColumnWidthTargets возвращает сохранённые ширины Ads Manager', () => {
    const widths = Object.fromEntries((0, ads_columns_js_1.buildAdsTableColumnWidthTargets)().map((target) => [target.key, target.widthPx]));
    strict_1.default.equal(widths.toggle, 40);
    strict_1.default.equal(widths.name, 194);
    strict_1.default.equal(widths.deposits, 137);
    strict_1.default.equal(widths.cost_per_registration, 100);
    strict_1.default.equal(widths.frequency, 40);
});
//# sourceMappingURL=ads-columns.test.js.map