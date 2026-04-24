"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const parser_js_1 = require("./parser.js");
function makeRow(overrides = {}) {
    return {
        fb_ad_id: '1234567890123',
        campaign_name: 'Кампания',
        adset_name: 'Группа',
        ad_name: 'Объявление',
        delivery_status: 'ACTIVE',
        spend: '0.10',
        budget: '100',
        reach: 1,
        impressions: 1,
        clicks: 1,
        cpc: '0.10',
        ctr: '1.0',
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
// Сценарий: если строки уже готовы, helper не должен делать лишние повторные чтения.
(0, node_test_1.default)('waitForParsedAdsRows сразу возвращает непустой результат', async () => {
    let attempts = 0;
    const rows = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 50,
        pollMs: 1,
        readRows: async () => {
            attempts += 1;
            return [makeRow()];
        },
    });
    strict_1.default.equal(rows.length, 1);
    strict_1.default.equal(attempts, 1);
});
// Сценарий: после краткого пустого состояния helper должен дождаться появления строк.
(0, node_test_1.default)('waitForParsedAdsRows повторяет чтение после временного нуля строк', async () => {
    let attempts = 0;
    const rows = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 100,
        pollMs: 1,
        readRows: async () => {
            attempts += 1;
            if (attempts < 3) {
                return [];
            }
            return [makeRow({ ad_name: 'DRC_CR2_CR010' })];
        },
    });
    strict_1.default.equal(rows.length, 1);
    strict_1.default.equal(rows[0]?.ad_name, 'DRC_CR2_CR010');
    strict_1.default.equal(attempts, 3);
});
// Сценарий: если строки так и не появились, helper должен вернуть пустой результат по таймауту.
(0, node_test_1.default)('waitForParsedAdsRows завершает ожидание пустым массивом по таймауту', async () => {
    let attempts = 0;
    const rows = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 10,
        pollMs: 1,
        readRows: async () => {
            attempts += 1;
            return [];
        },
    });
    strict_1.default.deepEqual(rows, []);
    strict_1.default.ok(attempts >= 1);
});
// Сценарий: выключенный тумблер имеет приоритет над текстом доставки и должен давать канонический OFF.
(0, node_test_1.default)('detectLogicalDeliveryStatus учитывает aria-checked тумблера', () => {
    strict_1.default.equal((0, parser_js_1.detectLogicalDeliveryStatus)('Показ кампании прекращен', 'false'), 'OFF');
    strict_1.default.equal((0, parser_js_1.detectLogicalDeliveryStatus)('Показ кампании прекращен', 'true'), 'NOT_DELIVERING');
});
//# sourceMappingURL=parser-retry.test.js.map