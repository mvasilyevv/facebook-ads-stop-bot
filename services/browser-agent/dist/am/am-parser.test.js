"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const am_parser_js_1 = require("./am-parser.js");
const am_join_js_1 = require("./am-join.js");
// Заголовки per-ad ответа am_tabular — точная копия боевой структуры (level=ad).
function amHeaders() {
    return {
        dimensions: ['objective', 'ad_id', 'date_start', 'date_stop'],
        atomic_columns: [
            { name: 'anchor_event_attribution_setting', type: 'string' },
            { name: 'multi_event_conversion_attribution_setting', type: 'string' },
            { name: 'reach', type: 'numeric string' },
            { name: 'impressions', type: 'numeric string' },
            { name: 'spend', type: 'numeric string' },
            { name: 'clicks', type: 'numeric string' },
            { name: 'cpc', type: 'numeric string' },
            { name: 'ctr', type: 'numeric string' },
            { name: 'cpm', type: 'numeric string' },
            { name: 'frequency', type: 'numeric string' },
            { name: 'attribution_setting', type: 'string' },
            { name: 'conversion_count_setting', type: 'string' },
        ],
        action_columns: [
            { name: 'actions', attribution_window: 'default' },
            { name: 'actions', attribution_window: 'inline' },
            { name: 'cost_per_action_type', attribution_window: 'default' },
            { name: 'cost_per_action_type', attribution_window: 'inline' },
            { name: 'outbound_clicks', attribution_window: 'default' },
            { name: 'outbound_clicks', attribution_window: 'inline' },
            { name: 'outbound_clicks_ctr', attribution_window: 'default' },
            { name: 'outbound_clicks_ctr', attribution_window: 'inline' },
            { name: 'conversion_annotations', attribution_window: 'default' },
            { name: 'conversion_annotations', attribution_window: 'inline' },
        ],
        result_columns: [
            { name: 'anchor_events', type: 'results', attribution_window: 'default' },
            { name: 'anchor_events', type: 'results', attribution_window: 'inline' },
            { name: 'results', type: 'results', attribution_window: 'default' },
            { name: 'results', type: 'results', attribution_window: 'inline' },
            { name: 'cost_per_result', type: 'results', attribution_window: 'default' },
            { name: 'cost_per_result', type: 'results', attribution_window: 'inline' },
        ],
    };
}
const EMPTY_ACTION = { breakdown: 'action_type' };
// Строка с конверсиями и результатом (ad ...570044 из live: dep=1, lead=1, reg=1).
function rowWithConversions() {
    return {
        dimension_values: ['OUTCOME_SALES', '120244531696570044', '2026-05-30', '2026-05-30'],
        atomic_values: ['na', 'na', '8', '8', '0.01', '2', '0.005', '25', '1.25', '1', '1d_click', 'ALL'],
        action_values: [
            {
                types: ['landing_page_view', 'lead', 'omni_landing_page_view', 'omni_complete_registration'],
                values: ['1', '1', '1', '1'],
                breakdown: 'action_type',
            },
            EMPTY_ACTION,
            {
                types: ['lead', 'omni_complete_registration', 'landing_page_view'],
                values: ['0.01', '0.01', '0.01'],
                breakdown: 'action_type',
            },
            EMPTY_ACTION,
            { types: ['outbound_click'], values: ['2'], breakdown: 'action_type' },
            EMPTY_ACTION,
            { types: ['outbound_click'], values: ['25'], breakdown: 'action_type' },
            EMPTY_ACTION,
            EMPTY_ACTION,
            EMPTY_ACTION,
        ],
        result_values: [
            { indicator: 'null' },
            { indicator: 'null' },
            { indicator: 'actions:offsite_conversion.fb_pixel_purchase', value: '1' },
            { indicator: 'actions:offsite_conversion.fb_pixel_purchase' },
            { indicator: 'actions:offsite_conversion.fb_pixel_purchase', value: '0.01' },
            { indicator: 'actions:offsite_conversion.fb_pixel_purchase' },
        ],
    };
}
// Пустая строка: метрики есть, конверсий/результата нет (action_values пустые, result_values null).
function rowEmpty() {
    return {
        dimension_values: ['OUTCOME_SALES', '120244530626090044', '2026-05-30', '2026-05-30'],
        atomic_values: ['na', 'na', '62', '63', '0.09', '0', 'null', '0', '1.428571', '1.016129', 'x', 'ALL'],
        action_values: [
            EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
            EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
        ],
        result_values: [
            { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
            { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
        ],
    };
}
// Summary-строка агрегата: ad_id="na" — должна отбрасываться.
function rowSummary() {
    return {
        dimension_values: ['OUTCOME_SALES', 'na', '2026-05-30', '2026-05-30'],
        atomic_values: ['multiple', '139', '999', '999', '99', '99', '0.1', '1.1', '14', '1.0', 'x', 'ALL'],
        action_values: [EMPTY_ACTION],
        result_values: [{ indicator: 'null' }],
    };
}
function amBody(rows) {
    return { data: [{ headers: amHeaders(), rows }] };
}
// parseAmTabular: summary-строка ad_id="na" отброшена, метрики/конверсии распарсены.
(0, node_test_1.default)('parseAmTabular: пропускает summary, парсит atomic+actions+result', () => {
    const rows = (0, am_parser_js_1.parseAmTabular)(amBody([rowSummary(), rowWithConversions(), rowEmpty()]));
    strict_1.default.equal(rows.length, 2); // summary выкинут
    const a = rows[0];
    strict_1.default.equal(a.adId, '120244531696570044');
    strict_1.default.equal(a.atomic.spend, '0.01');
    strict_1.default.equal(a.atomic.impressions, '8');
    strict_1.default.equal(a.atomic.clicks, '2');
    strict_1.default.equal(a.actions.lead, '1');
    strict_1.default.equal(a.actions.omni_complete_registration, '1');
    strict_1.default.equal(a.actions.landing_page_view, '1');
    strict_1.default.equal(a.costPerAction.lead, '0.01');
    strict_1.default.equal(a.outboundClicks, '2');
    strict_1.default.equal(a.outboundCtr, '25');
    // deposits ← result_columns[name=results, default] = slot 2
    strict_1.default.equal(a.results, '1');
    strict_1.default.equal(a.costPerResult, '0.01');
});
// parseAmTabular: пустая строка — atomic есть, конверсий нет (null/na отфильтрованы).
(0, node_test_1.default)('parseAmTabular: пустые конверсии → пустые dict, results=null', () => {
    const rows = (0, am_parser_js_1.parseAmTabular)(amBody([rowEmpty()]));
    const r = rows[0];
    strict_1.default.equal(r.atomic.spend, '0.09');
    strict_1.default.equal(r.atomic.clicks, '0');
    strict_1.default.equal(r.atomic.cpc, undefined); // "null" отфильтрован
    strict_1.default.deepEqual(r.actions, {});
    strict_1.default.equal(r.results, null);
    strict_1.default.equal(r.costPerResult, null);
});
// parseAmTabular: ответ без ad_id в dimensions (footer level=account) → пусто.
(0, node_test_1.default)('parseAmTabular: footer без ad_id игнорируется', () => {
    const footer = {
        data: [{ headers: { dimensions: ['objective', 'date_start', 'date_stop'] }, rows: [{}] }],
    };
    strict_1.default.deepEqual((0, am_parser_js_1.parseAmTabular)(footer), []);
    strict_1.default.deepEqual((0, am_parser_js_1.parseAmTabular)(null), []);
    strict_1.default.deepEqual((0, am_parser_js_1.parseAmTabular)({}), []);
});
// mergeAmRows: sync даёт result, async добивает actions — мёрж по ad_id берёт оба.
(0, node_test_1.default)('mergeAmRows: sync(result) + async(actions) → объединение', () => {
    const sync = (0, am_parser_js_1.parseAmTabular)(amBody([
        {
            dimension_values: ['OUTCOME_SALES', 'AD1', 'd', 'd'],
            atomic_values: ['na', 'na', '10', '10', '0.50', '3', '0.16', '30', '50', '1', 'x', 'ALL'],
            action_values: [EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
                EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION],
            result_values: [{ indicator: 'null' }, { indicator: 'null' },
                { indicator: 'x', value: '2' }, { indicator: 'x' }, { indicator: 'x', value: '0.25' }, { indicator: 'x' }],
        },
    ]));
    const asyncRows = (0, am_parser_js_1.parseAmTabular)(amBody([
        {
            dimension_values: ['OUTCOME_SALES', 'AD1', 'd', 'd'],
            atomic_values: ['na', 'na', '10', '10', '0.50', '3', '0.16', '30', '50', '1', 'x', 'ALL'],
            action_values: [
                { types: ['lead', 'omni_complete_registration'], values: ['5', '4'], breakdown: 'action_type' },
                EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
                EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
            ],
            result_values: [{ indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
                { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' }],
        },
    ]));
    const merged = (0, am_parser_js_1.mergeAmRows)([...sync, ...asyncRows]);
    strict_1.default.equal(merged.size, 1);
    const r = merged.get('AD1');
    strict_1.default.equal(r.actions.lead, '5'); // из async
    strict_1.default.equal(r.results, '2'); // из sync (async result=null, не затёр)
});
// buildScannedRow: полный маппинг в ScannedAdRow — deposits=results, числа форматируются как DOM.
(0, node_test_1.default)('buildScannedRow: маппинг конверсий и метрик', () => {
    const [am] = (0, am_parser_js_1.parseAmTabular)(amBody([rowWithConversions()]));
    const row = (0, am_join_js_1.buildScannedRow)(am, {
        adName: 'KE_CR2_a8',
        adsetName: 'adset-1',
        campaignName: 'CR2 | KE | MV | 30.05',
        campaignId: '120203451234560078',
        effectiveStatus: 'ACTIVE',
        budget: '$5',
    });
    strict_1.default.equal(row.fb_ad_id, '120244531696570044');
    strict_1.default.equal(row.campaign_name, 'CR2 | KE | MV | 30.05');
    // campaign_id прокидывается в каталог (allowlist «Кампании для сканирования»).
    strict_1.default.equal(row.campaign_id, '120203451234560078');
    strict_1.default.equal(row.delivery_status, 'ACTIVE');
    strict_1.default.equal(row.spend, '0.01');
    strict_1.default.equal(row.impressions, 8);
    strict_1.default.equal(row.clicks, 2);
    strict_1.default.equal(row.cpc, '0.005');
    strict_1.default.equal(row.ctr, '25');
    strict_1.default.equal(row.cpm, '1.25');
    strict_1.default.equal(row.frequency, '1');
    strict_1.default.equal(row.leads, 1);
    strict_1.default.equal(row.registrations, 1);
    strict_1.default.equal(row.landing_page_views, 1);
    strict_1.default.equal(row.outbound_clicks, 2);
    strict_1.default.equal(row.deposits, 1); // ← result_values[results,default]
    strict_1.default.equal(row.cost_per_result, '0.01');
    strict_1.default.equal(row.resolved_offer_code, null);
});
// buildScannedRow: пустая строка → нулевые конверсии, deposits=0, cpc=null.
(0, node_test_1.default)('buildScannedRow: пустая строка → нули и null', () => {
    const [am] = (0, am_parser_js_1.parseAmTabular)(amBody([rowEmpty()]));
    const row = (0, am_join_js_1.buildScannedRow)(am, { adName: 'x', campaignName: 'c' });
    strict_1.default.equal(row.spend, '0.09');
    strict_1.default.equal(row.clicks, 0);
    strict_1.default.equal(row.cpc, null);
    strict_1.default.equal(row.leads, 0);
    strict_1.default.equal(row.registrations, 0);
    strict_1.default.equal(row.deposits, 0);
    strict_1.default.equal(row.cost_per_result, null);
});
// buildScannedRows: батч merged-строк + карта meta.
(0, node_test_1.default)('buildScannedRows: батч + meta по ad_id', () => {
    const merged = (0, am_parser_js_1.mergeAmRows)((0, am_parser_js_1.parseAmTabular)(amBody([rowWithConversions(), rowEmpty()])));
    const meta = new Map([['120244531696570044', { adName: 'A', campaignName: 'CR2' }]]);
    const rows = (0, am_join_js_1.buildScannedRows)(merged, meta);
    strict_1.default.equal(rows.length, 2);
    const first = rows.find((r) => r.fb_ad_id === '120244531696570044');
    strict_1.default.equal(first.ad_name, 'A');
    const second = rows.find((r) => r.fb_ad_id === '120244530626090044');
    strict_1.default.equal(second.ad_name, ''); // нет meta → пустое
});
// mapEffectiveStatus: коды FB → стабильные delivery_status (без зависимости от локали).
(0, node_test_1.default)('mapEffectiveStatus: коды статусов', () => {
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ACTIVE'), 'ACTIVE');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('PAUSED'), 'OFF');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ADSET_PAUSED'), 'OFF');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('CAMPAIGN_PAUSED'), 'OFF');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ARCHIVED'), 'OFF');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('PENDING_REVIEW'), 'IN_REVIEW');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('DISAPPROVED'), 'NOT_DELIVERING');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('WITH_ISSUES'), 'NOT_DELIVERING');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)(''), 'UNKNOWN');
    strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)(undefined), 'UNKNOWN');
});
// parseLightList: id-only (fields=id) и расширенный (name/effective_status).
(0, node_test_1.default)('parseLightList: id-only и расширенный', () => {
    const idOnly = (0, am_parser_js_1.parseLightList)({ data: [{ id: '111' }, { id: '222' }], paging: {} });
    strict_1.default.equal(idOnly.length, 2);
    strict_1.default.equal(idOnly[0].id, '111');
    strict_1.default.equal(idOnly[0].name, undefined);
    const rich = (0, am_parser_js_1.parseLightList)({
        data: [
            {
                id: '111',
                name: 'Camp A',
                effective_status: 'ACTIVE',
                daily_budget: '500',
                campaign_id: '900',
                adset_id: '800',
            },
        ],
    });
    strict_1.default.equal(rich[0].name, 'Camp A');
    strict_1.default.equal(rich[0].effectiveStatus, 'ACTIVE');
    strict_1.default.equal(rich[0].dailyBudget, '500');
    strict_1.default.equal(rich[0].campaignId, '900');
    strict_1.default.equal(rich[0].adsetId, '800');
    strict_1.default.deepEqual((0, am_parser_js_1.parseLightList)(null), []);
    strict_1.default.deepEqual((0, am_parser_js_1.parseLightList)({ data: 'x' }), []);
});
// lightNextCursor: курсор пагинации из paging.cursors.after.
(0, node_test_1.default)('lightNextCursor: курсор after', () => {
    strict_1.default.equal((0, am_parser_js_1.lightNextCursor)({ paging: { cursors: { after: 'CUR123' } } }), 'CUR123');
    strict_1.default.equal((0, am_parser_js_1.lightNextCursor)({ paging: {} }), null);
    strict_1.default.equal((0, am_parser_js_1.lightNextCursor)({}), null);
});
//# sourceMappingURL=am-parser.test.js.map