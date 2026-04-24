"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.REQUIRED_COLUMNS = void 0;
exports.normalizeVisibleHeaders = normalizeVisibleHeaders;
exports.collectFoundValidationColumns = collectFoundValidationColumns;
exports.collectMissingValidationColumns = collectMissingValidationColumns;
exports.buildParserColumnLayout = buildParserColumnLayout;
const COLUMN_SPECS = [
    { key: 'toggle', title: 'Выкл./вкл.', surfaceKey: 'toggle' },
    { key: 'name', title: 'Название объявления', surfaceKey: 'name', parserField: 'ad_name', valueKind: 'name' },
    { key: 'delivery', title: 'Статус показа', surfaceKey: 'delivery', parserField: 'delivery_status', valueKind: 'text' },
    { key: 'budget', title: 'Бюджет', surfaceKey: 'budget', parserField: 'budget', valueKind: 'text' },
    { key: 'reach', title: 'Охват', surfaceKey: 'reach', parserField: 'reach', valueKind: 'metric' },
    { key: 'impressions', title: 'Показы', surfaceKey: 'impressions', parserField: 'impressions', valueKind: 'metric' },
    { key: 'cost_per_result', title: 'Цена за результат', surfaceKey: 'cost_per_result', parserField: 'cost_per_result', valueKind: 'metric' },
    { key: 'spend', title: 'Сумма затрат', surfaceKey: 'spend', parserField: 'spend', valueKind: 'metric' },
    { key: 'clicks', title: 'Клики', surfaceKey: 'clicks', parserField: 'clicks', valueKind: 'metric' },
    { key: 'cpc', title: 'CPC', surfaceKey: 'cpc', parserField: 'cpc', valueKind: 'metric' },
    { key: 'leads', title: 'Лиды', surfaceKey: 'actions', textNeedles: ['лид', 'лід', 'lead'], parserField: 'leads', valueKind: 'metric' },
    { key: 'cost_per_lead', title: 'Цена за лид', surfaceKey: 'cost_per_action_type', textNeedles: ['лид', 'лід', 'lead'], parserField: 'cost_per_lead', valueKind: 'metric' },
    {
        key: 'registrations',
        title: 'Завершенные регистрации',
        surfaceKey: 'actions',
        textNeedles: ['регистрац', 'реєстрац', 'registration'],
        parserField: 'registrations',
        valueKind: 'metric',
    },
    {
        key: 'cost_per_registration',
        title: 'Цена за завершенную регистрацию',
        surfaceKey: 'cost_per_action_type',
        textNeedles: ['регистрац', 'реєстрац', 'registration'],
        parserField: 'cost_per_registration',
        valueKind: 'metric',
    },
    { key: 'ctr', title: 'CTR', surfaceKey: 'ctr', parserField: 'ctr', valueKind: 'metric' },
    { key: 'campaign_name', title: 'Название кампании', surfaceKey: 'campaign_group_name', parserField: 'campaign_name', valueKind: 'text' },
    { key: 'adset_name', title: 'Название группы объявлений', surfaceKey: 'campaign_name', parserField: 'adset_name', valueKind: 'text' },
    {
        key: 'outbound_clicks',
        title: 'Исходящие клики',
        surfaceKey: 'outbound_clicks',
        parserField: 'outbound_clicks',
        valueKind: 'metric',
    },
    {
        key: 'outbound_ctr',
        title: 'CTR исходящих кликов',
        surfaceKey: 'outbound_clicks_ctr',
        parserField: 'outbound_ctr',
        valueKind: 'metric',
    },
    {
        key: 'landing_page_views',
        title: 'Просмотры целевой страницы',
        surfaceKey: 'actions',
        textNeedles: ['целев', 'цільов', 'landing page'],
        parserField: 'landing_page_views',
        valueKind: 'metric',
    },
    {
        key: 'cost_per_landing_page_view',
        title: 'Цена за просмотр целевой страницы',
        surfaceKey: 'cost_per_action_type',
        textNeedles: ['целев', 'цільов', 'landing page'],
        parserField: 'cost_per_landing_page_view',
        valueKind: 'metric',
    },
    { key: 'cpm', title: 'CPM', surfaceKey: 'cpm', parserField: 'cpm', valueKind: 'metric' },
    { key: 'frequency', title: 'Частота', surfaceKey: 'frequency', parserField: 'frequency', valueKind: 'metric' },
];
const VALIDATION_SPECS = COLUMN_SPECS.filter((spec) => spec.requiredForValidation !== false);
const PARSER_SPECS = COLUMN_SPECS.filter((spec) => Boolean(spec.parserField));
exports.REQUIRED_COLUMNS = VALIDATION_SPECS.map((column) => column.key);
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
    const missingColumns = PARSER_SPECS
        .filter((spec) => !presentKeys.has(spec.key))
        .map((spec) => spec.title);
    return {
        headerCount: visibleHeaders.length,
        layout,
        missingColumns,
    };
}
//# sourceMappingURL=ads-columns.js.map