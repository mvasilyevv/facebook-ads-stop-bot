"use strict";
// H-8 (BA-3): money-критичный маппинг am_tabular → ScannedAdRow. Регресс на класс
// «shape прошёл, семантика сломалась»: метрики/конверсии напрямую кормят стоп-правила.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const am_join_js_1 = require("./am-join.js");
function amRow(over = {}) {
    return {
        adId: '123',
        objective: null,
        atomic: {},
        actions: {},
        costPerAction: {},
        outboundClicks: null,
        outboundCtr: null,
        results: null,
        costPerResult: null,
        ...over,
    };
}
(0, node_test_1.describe)('buildScannedRow money-маппинг (H-8)', () => {
    (0, node_test_1.it)('полная строка: метрики + конверсии + meta', () => {
        const row = (0, am_join_js_1.buildScannedRow)(amRow({
            adId: '777',
            atomic: {
                spend: '12.34',
                impressions: '1000',
                reach: '900',
                clicks: '50',
                cpc: '0.24',
                ctr: '5.0',
                cpm: '12.3',
                frequency: '1.11',
            },
            actions: { lead: '7', omni_complete_registration: '3', landing_page_view: '20' },
            costPerAction: { lead: '1.76', omni_complete_registration: '4.11', landing_page_view: '0.61' },
            outboundClicks: '40',
            outboundCtr: '4.0',
            results: '2',
            costPerResult: '6.17',
        }), {
            adName: 'Ad X',
            campaignName: 'CR2 | KE | MV',
            campaignId: 'c1',
            adsetName: 'as1',
            effectiveStatus: 'ACTIVE',
        });
        strict_1.default.equal(row.fb_ad_id, '777');
        strict_1.default.equal(row.spend, '12.34');
        strict_1.default.equal(row.impressions, 1000);
        strict_1.default.equal(row.reach, 900);
        strict_1.default.equal(row.clicks, 50);
        strict_1.default.equal(row.cpc, '0.24');
        strict_1.default.equal(row.cpm, '12.3');
        strict_1.default.equal(row.frequency, '1.11');
        // Конверсии: лиды/реги/LPV из своих action_type, depo из results.
        strict_1.default.equal(row.leads, 7);
        strict_1.default.equal(row.cost_per_lead, '1.76');
        strict_1.default.equal(row.registrations, 3);
        strict_1.default.equal(row.cost_per_registration, '4.11');
        strict_1.default.equal(row.landing_page_views, 20);
        strict_1.default.equal(row.cost_per_landing_page_view, '0.61');
        strict_1.default.equal(row.deposits, 2);
        strict_1.default.equal(row.cost_per_result, '6.17');
        strict_1.default.equal(row.outbound_clicks, 40);
        // Meta.
        strict_1.default.equal(row.delivery_status, 'ACTIVE');
        strict_1.default.equal(row.campaign_name, 'CR2 | KE | MV');
        strict_1.default.equal(row.ad_name, 'Ad X');
    });
    (0, node_test_1.it)('пустая строка: spend дефолтит "0", счётчики 0, опц. Decimal → null', () => {
        const row = (0, am_join_js_1.buildScannedRow)(amRow({ adId: 'e' }));
        strict_1.default.equal(row.spend, '0'); // money всегда заполнен (как в DOM-пути)
        strict_1.default.equal(row.impressions, 0);
        strict_1.default.equal(row.leads, 0);
        strict_1.default.equal(row.registrations, 0);
        strict_1.default.equal(row.deposits, 0);
        strict_1.default.equal(row.cpc, null); // отсутствует → null, не "0"
        strict_1.default.equal(row.cost_per_lead, null);
        strict_1.default.equal(row.delivery_status, 'UNKNOWN'); // нет статуса
        strict_1.default.equal(row.campaign_name, ''); // нет meta
        strict_1.default.equal(row.ad_name, '');
    });
    (0, node_test_1.it)('мелкие десятичные НЕ ломаются locale-эвристикой ("0.005" остаётся)', () => {
        const row = (0, am_join_js_1.buildScannedRow)(amRow({ atomic: { cpc: '0.005', spend: '0.01', ctr: '0.50' } }));
        strict_1.default.equal(row.cpc, '0.005');
        strict_1.default.equal(row.spend, '0.01');
        strict_1.default.equal(row.ctr, '0.50');
    });
    (0, node_test_1.it)('amInt: нечисло → 0, дробное → trunc', () => {
        strict_1.default.equal((0, am_join_js_1.buildScannedRow)(amRow({ atomic: { impressions: 'abc' } })).impressions, 0);
        strict_1.default.equal((0, am_join_js_1.buildScannedRow)(amRow({ atomic: { impressions: '1000.9' } })).impressions, 1000);
    });
});
(0, node_test_1.describe)('mapEffectiveStatus (H-8)', () => {
    (0, node_test_1.it)('известные статусы → канон', () => {
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ACTIVE'), 'ACTIVE');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('PAUSED'), 'OFF');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ADSET_PAUSED'), 'OFF');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('ARCHIVED'), 'OFF');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('PENDING_REVIEW'), 'IN_REVIEW');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('DISAPPROVED'), 'NOT_DELIVERING');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('IN_PROCESS'), 'PROCESSING');
    });
    (0, node_test_1.it)('пусто → UNKNOWN, неизвестное → passthrough (uppercase)', () => {
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)(''), 'UNKNOWN');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)(undefined), 'UNKNOWN');
        strict_1.default.equal((0, am_join_js_1.mapEffectiveStatus)('some_new_status'), 'SOME_NEW_STATUS');
    });
});
(0, node_test_1.describe)('buildScannedRows (H-8)', () => {
    (0, node_test_1.it)('итерирует merged-карту + резолвит meta по adId', () => {
        const merged = new Map([
            ['a1', amRow({ adId: 'a1', atomic: { spend: '1' } })],
            ['a2', amRow({ adId: 'a2', atomic: { spend: '2' } })],
        ]);
        const adMeta = new Map([['a1', { adName: 'A1', effectiveStatus: 'ACTIVE' }]]);
        const rows = (0, am_join_js_1.buildScannedRows)(merged, adMeta);
        strict_1.default.equal(rows.length, 2);
        const r1 = rows.find((r) => r.fb_ad_id === 'a1');
        const r2 = rows.find((r) => r.fb_ad_id === 'a2');
        strict_1.default.equal(r1?.ad_name, 'A1');
        strict_1.default.equal(r1?.delivery_status, 'ACTIVE');
        strict_1.default.equal(r2?.ad_name, ''); // нет meta → дефолты
        strict_1.default.equal(r2?.delivery_status, 'UNKNOWN');
    });
});
//# sourceMappingURL=am-join.test.js.map