"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.REQUIRED_COLUMNS = void 0;
exports.buildAdsTableColumnWidthTargets = buildAdsTableColumnWidthTargets;
exports.normalizeVisibleHeaders = normalizeVisibleHeaders;
exports.collectFoundValidationColumns = collectFoundValidationColumns;
exports.collectMissingValidationColumns = collectMissingValidationColumns;
exports.buildParserColumnLayout = buildParserColumnLayout;
const COLUMN_SPECS = [
    { key: 'toggle', title: 'Выкл./вкл.', surfaceKey: 'toggle', widthPx: 40 },
    {
        key: 'name',
        title: 'Название объявления',
        surfaceKey: 'name',
        parserField: 'ad_name',
        valueKind: 'name',
        widthPx: 194,
    },
    {
        key: 'delivery',
        title: 'Статус показа',
        surfaceKey: 'delivery',
        parserField: 'delivery_status',
        valueKind: 'text',
        widthPx: 110,
    },
    {
        key: 'budget',
        title: 'Бюджет',
        surfaceKey: 'budget',
        parserField: 'budget',
        valueKind: 'text',
        widthPx: 40,
    },
    {
        key: 'deposits',
        title: 'Результат',
        surfaceKey: 'results',
        parserField: 'deposits',
        valueKind: 'metric',
        widthPx: 137,
    },
    {
        key: 'reach',
        title: 'Охват',
        surfaceKey: 'reach',
        parserField: 'reach',
        valueKind: 'metric',
        widthPx: 99,
    },
    {
        key: 'impressions',
        title: 'Показы',
        surfaceKey: 'impressions',
        parserField: 'impressions',
        valueKind: 'metric',
        widthPx: 113,
    },
    {
        key: 'cost_per_result',
        title: 'Цена за результат',
        surfaceKey: 'cost_per_result',
        parserField: 'cost_per_result',
        valueKind: 'metric',
        widthPx: 130,
    },
    {
        key: 'spend',
        title: 'Сумма затрат',
        surfaceKey: 'spend',
        parserField: 'spend',
        valueKind: 'metric',
        widthPx: 112,
    },
    {
        key: 'clicks',
        title: 'Клики',
        surfaceKey: 'clicks',
        parserField: 'clicks',
        valueKind: 'metric',
        widthPx: 102,
    },
    {
        key: 'cpc',
        title: 'CPC',
        surfaceKey: 'cpc',
        parserField: 'cpc',
        valueKind: 'metric',
        widthPx: 93,
    },
    {
        key: 'leads',
        title: 'Лиды',
        surfaceKey: 'actions',
        textNeedles: ['лид', 'лід', 'lead'],
        parserField: 'leads',
        valueKind: 'metric',
        widthPx: 102,
    },
    {
        key: 'cost_per_lead',
        title: 'Цена за лид',
        surfaceKey: 'cost_per_action_type',
        textNeedles: ['лид', 'лід', 'lead'],
        parserField: 'cost_per_lead',
        valueKind: 'metric',
        widthPx: 105,
    },
    {
        key: 'registrations',
        title: 'Завершенные регистрации',
        surfaceKey: 'actions',
        textNeedles: ['регистрац', 'реєстрац', 'registration'],
        parserField: 'registrations',
        valueKind: 'metric',
        widthPx: 88,
    },
    {
        key: 'cost_per_registration',
        title: 'Цена за завершенную регистрацию',
        surfaceKey: 'cost_per_action_type',
        textNeedles: ['регистрац', 'реєстрац', 'registration'],
        parserField: 'cost_per_registration',
        valueKind: 'metric',
        widthPx: 100,
    },
    {
        key: 'ctr',
        title: 'CTR',
        surfaceKey: 'ctr',
        parserField: 'ctr',
        valueKind: 'metric',
        widthPx: 91,
    },
    {
        key: 'campaign_name',
        title: 'Название кампании',
        surfaceKey: 'campaign_group_name',
        parserField: 'campaign_name',
        valueKind: 'text',
        widthPx: 40,
    },
    {
        key: 'adset_name',
        title: 'Название группы объявлений',
        surfaceKey: 'campaign_name',
        parserField: 'adset_name',
        valueKind: 'text',
        widthPx: 40,
    },
    {
        key: 'outbound_clicks',
        title: 'Исходящие клики',
        surfaceKey: 'outbound_clicks',
        parserField: 'outbound_clicks',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
    {
        key: 'outbound_ctr',
        title: 'CTR исходящих кликов',
        surfaceKey: 'outbound_clicks_ctr',
        parserField: 'outbound_ctr',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
    {
        key: 'landing_page_views',
        title: 'Просмотры целевой страницы',
        surfaceKey: 'actions',
        textNeedles: ['целев', 'цільов', 'landing page'],
        parserField: 'landing_page_views',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
    {
        key: 'cost_per_landing_page_view',
        title: 'Цена за просмотр целевой страницы',
        surfaceKey: 'cost_per_action_type',
        textNeedles: ['целев', 'цільов', 'landing page'],
        parserField: 'cost_per_landing_page_view',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
    {
        key: 'cpm',
        title: 'CPM',
        surfaceKey: 'cpm',
        parserField: 'cpm',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
    {
        key: 'frequency',
        title: 'Частота',
        surfaceKey: 'frequency',
        parserField: 'frequency',
        valueKind: 'metric',
        requiredForValidation: false,
        requiredForParsing: false,
        widthPx: 40,
    },
];
const VALIDATION_SPECS = COLUMN_SPECS.filter((spec) => spec.requiredForValidation !== false);
const PARSER_SPECS = COLUMN_SPECS.filter((spec) => Boolean(spec.parserField));
const REQUIRED_PARSER_SPECS = PARSER_SPECS.filter((spec) => spec.requiredForParsing !== false);
exports.REQUIRED_COLUMNS = VALIDATION_SPECS.map((column) => column.key);
function buildAdsTableColumnWidthTargets() {
    return COLUMN_SPECS
        .filter((spec) => Number.isFinite(spec.widthPx))
        .map((spec) => ({
        key: spec.key,
        title: spec.title,
        surfaceKey: spec.surfaceKey,
        textNeedles: spec.textNeedles,
        widthPx: spec.widthPx,
    }));
}
function normalizeHeaderText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}
function matchesHeaderSpec(header, spec) {
    if (header.surfaceKey !== spec.surfaceKey)
        return false;
    if (!spec.textNeedles?.length)
        return true;
    return spec.textNeedles.some((needle) => header.text.includes(needle));
}
function normalizeVisibleHeaders(headers) {
    const normalized = headers
        .map((header) => ({
        surfaceKey: String(header.surfaceKey || '').trim(),
        text: normalizeHeaderText(header.text || ''),
        left: Number(header.left) || 0,
    }))
        .filter((header) => header.surfaceKey || header.text)
        .sort((left, right) => left.left - right.left);
    const nonEmptySurfaceKeys = new Set(normalized
        .filter((header) => header.surfaceKey && header.text)
        .map((header) => header.surfaceKey));
    const deduped = [];
    for (const header of normalized) {
        if (!header.text && header.surfaceKey && nonEmptySurfaceKeys.has(header.surfaceKey)) {
            continue;
        }
        const prev = deduped[deduped.length - 1];
        if (prev
            && prev.surfaceKey === header.surfaceKey
            && prev.text === header.text
            && Math.abs(prev.left - header.left) < 2) {
            continue;
        }
        deduped.push(header);
    }
    return deduped;
}
function findHeaderSpec(header) {
    return COLUMN_SPECS.find((spec) => matchesHeaderSpec(header, spec));
}
function collectFoundValidationColumns(headers) {
    const found = new Set();
    for (const header of normalizeVisibleHeaders(headers)) {
        const spec = findHeaderSpec(header);
        if (spec && spec.requiredForValidation !== false)
            found.add(spec.key);
    }
    return Array.from(found);
}
function collectMissingValidationColumns(headers) {
    const found = new Set(collectFoundValidationColumns(headers));
    return VALIDATION_SPECS
        .filter((spec) => !found.has(spec.key))
        .map((spec) => spec.title);
}
function buildParserColumnLayout(headers) {
    const visibleHeaders = normalizeVisibleHeaders(headers);
    const layout = [];
    const presentKeys = new Set();
    visibleHeaders.forEach((header, headerIndex) => {
        const spec = findHeaderSpec(header);
        if (!spec?.parserField)
            return;
        presentKeys.add(spec.key);
        layout.push({
            headerIndex,
            key: spec.key,
            title: spec.title,
            fieldName: spec.parserField,
            valueKind: spec.valueKind || 'metric',
        });
    });
    const missingColumns = REQUIRED_PARSER_SPECS
        .filter((spec) => !presentKeys.has(spec.key))
        .map((spec) => spec.title);
    return {
        headerCount: visibleHeaders.length,
        layout,
        missingColumns,
    };
}
//# sourceMappingURL=ads-columns.js.map