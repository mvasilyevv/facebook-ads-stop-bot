"use strict";
// Тесты helpers countEmptyMetricsRows и findPartialRows из parser.ts.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const parser_js_1 = require("./parser.js");
function makeRow(overrides) {
    return {
        fb_ad_id: '1',
        campaign_name: 'c',
        adset_name: 'a',
        ad_name: 'n',
        delivery_status: 'Активно',
        spend: '',
        budget: '',
        reach: 0,
        impressions: 0,
        clicks: 0,
        cpc: null,
        ctr: null,
        outbound_clicks: 0,
        outbound_ctr: null,
        landing_page_views: 0,
        cost_per_landing_page_view: null,
        cost_per_result: null,
        cpm: null,
        frequency: null,
        leads: 0,
        cost_per_lead: null,
        registrations: 0,
        cost_per_registration: null,
        deposits: 0,
        resolved_offer_code: null,
        ...overrides,
    };
}
(0, node_test_1.describe)('countEmptyMetricsRows', () => {
    (0, node_test_1.it)('считает строку пустой, если все критические метрики = "" / "—" / null / 0', () => {
        const row = makeRow({ impressions: 0, spend: '', cpm: '—', cpc: '', ctr: '' });
        strict_1.default.equal((0, parser_js_1.countEmptyMetricsRows)([row]), 1);
    });
    (0, node_test_1.it)('считает строку пустой при null-значениях метрик', () => {
        const row = makeRow({ impressions: 0, spend: '', cpm: null, cpc: null, ctr: null });
        strict_1.default.equal((0, parser_js_1.countEmptyMetricsRows)([row]), 1);
    });
    (0, node_test_1.it)('не считает пустой, если хотя бы одна критическая метрика непустая', () => {
        const row = makeRow({ impressions: 100, spend: '' });
        strict_1.default.equal((0, parser_js_1.countEmptyMetricsRows)([row]), 0);
    });
    (0, node_test_1.it)('не считает пустой, если spend > 0', () => {
        const row = makeRow({ impressions: 0, spend: '5.50' });
        strict_1.default.equal((0, parser_js_1.countEmptyMetricsRows)([row]), 0);
    });
});
(0, node_test_1.describe)('findPartialRows', () => {
    (0, node_test_1.it)('возвращает fb_ad_id строк с пустыми ad_name или campaign_name', () => {
        const rows = [
            makeRow({ fb_ad_id: '1', ad_name: '', campaign_name: 'c' }),
            makeRow({ fb_ad_id: '2', ad_name: 'n', campaign_name: 'c' }),
            makeRow({ fb_ad_id: '3', ad_name: 'n', campaign_name: '' }),
        ];
        strict_1.default.deepEqual((0, parser_js_1.findPartialRows)(rows), ['1', '3']);
    });
    (0, node_test_1.it)('пропускает строки без fb_ad_id', () => {
        const rows = [makeRow({ fb_ad_id: '', ad_name: '', campaign_name: '' })];
        strict_1.default.deepEqual((0, parser_js_1.findPartialRows)(rows), []);
    });
});
//# sourceMappingURL=parser-stale.test.js.map