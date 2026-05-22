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
            return { rows: [makeRow()], partialRowIds: [] };
        },
    });
    strict_1.default.equal(rows.rows.length, 1);
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
                return { rows: [], partialRowIds: [] };
            }
            return { rows: [makeRow({ ad_name: 'DRC_CR2_CR010' })], partialRowIds: [] };
        },
    });
    strict_1.default.equal(rows.rows.length, 1);
    strict_1.default.equal(rows.rows[0]?.ad_name, 'DRC_CR2_CR010');
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
            return { rows: [], partialRowIds: [] };
        },
    });
    strict_1.default.deepEqual(rows.rows, []);
    strict_1.default.deepEqual(rows.partialRowIds, []);
    strict_1.default.ok(attempts >= 1);
});
// Сценарий: выключенный тумблер имеет приоритет над текстом доставки и должен давать канонический OFF.
(0, node_test_1.default)('detectLogicalDeliveryStatus учитывает aria-checked тумблера', () => {
    strict_1.default.equal((0, parser_js_1.detectLogicalDeliveryStatus)('Показ кампании прекращен', 'false'), 'OFF');
    strict_1.default.equal((0, parser_js_1.detectLogicalDeliveryStatus)('Показ кампании прекращен', 'true'), 'NOT_DELIVERING');
});
// Сценарий: если при чтении возникает ошибка (например, колонка CPM ещё не прогрузилась), но затем загружается успешно
(0, node_test_1.default)('waitForParsedAdsRows пробует повторить чтение при ошибке и возвращает строки при успехе', async () => {
    let attempts = 0;
    const rows = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 100,
        pollMs: 1,
        readRows: async () => {
            attempts += 1;
            if (attempts < 3) {
                throw new Error('Не удалось распарсить таблицу Ads Manager: отсутствуют обязательные колонки: CPM');
            }
            return { rows: [makeRow({ cpm: '15.50' })], partialRowIds: [] };
        },
    });
    strict_1.default.equal(rows.rows.length, 1);
    strict_1.default.equal(rows.rows[0]?.cpm, '15.50');
    strict_1.default.equal(attempts, 3);
});
// Сценарий: если при чтении постоянно возникают ошибки вплоть до таймаута, выбрасывается последняя ошибка
(0, node_test_1.default)('waitForParsedAdsRows пробрасывает ошибку парсинга наружу по истечении таймаута', async () => {
    let attempts = 0;
    await strict_1.default.rejects(async () => {
        await (0, parser_js_1.waitForParsedAdsRows)({}, {
            timeoutMs: 20,
            pollMs: 1,
            readRows: async () => {
                attempts += 1;
                throw new Error('Фатальный сбой: колонка CPM отсутствует');
            },
        });
    }, (err) => {
        strict_1.default.equal(err.message, 'Фатальный сбой: колонка CPM отсутствует');
        return true;
    });
    strict_1.default.ok(attempts >= 1);
});
// Сценарий: если ячейка в spinner-загрузке, парсер возвращает строку с пустым полем
// и кладёт fb_ad_id в partialRowIds. С maxPartialRatio=1.0 любая доля приемлема —
// возвращаем сразу (без adaptive wait).
(0, node_test_1.default)('waitForParsedAdsRows возвращает partialRowIds для строки со spinner-метрикой', async () => {
    let attempts = 0;
    const result = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 100,
        pollMs: 1,
        maxPartialRatio: 1.0,
        readRows: async () => {
            attempts += 1;
            return {
                rows: [makeRow({ fb_ad_id: '120243762575150044', spend: '' })],
                partialRowIds: ['120243762575150044'],
            };
        },
    });
    strict_1.default.equal(result.rows.length, 1);
    strict_1.default.deepEqual(result.partialRowIds, ['120243762575150044']);
    strict_1.default.equal(attempts, 1);
});
// Сценарий adaptive wait: если первая попытка дала 100% partial, ждём ещё и возвращаем
// результат когда доля partial упадёт ниже maxPartialRatio.
(0, node_test_1.default)('waitForParsedAdsRows ждёт пока partial-доля упадёт ниже порога', async () => {
    let attempts = 0;
    const result = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 200,
        pollMs: 5,
        maxPartialRatio: 0.5,
        readRows: async () => {
            attempts += 1;
            // На 1-2 попытках все строки partial; на 3-й только 1/3.
            if (attempts <= 2) {
                return {
                    rows: [makeRow({ fb_ad_id: '1' }), makeRow({ fb_ad_id: '2' }), makeRow({ fb_ad_id: '3' })],
                    partialRowIds: ['1', '2', '3'],
                };
            }
            return {
                rows: [makeRow({ fb_ad_id: '1' }), makeRow({ fb_ad_id: '2' }), makeRow({ fb_ad_id: '3' })],
                partialRowIds: ['3'],
            };
        },
    });
    strict_1.default.equal(result.rows.length, 3);
    strict_1.default.deepEqual(result.partialRowIds, ['3']);
    strict_1.default.equal(attempts, 3);
});
// Сценарий timeout: если partial-доля так и не упала ниже порога, возвращаем best-so-far
// (тот результат, где partial был минимальным) — не throw, не пусто.
(0, node_test_1.default)('waitForParsedAdsRows по таймауту возвращает best-so-far результат', async () => {
    let attempts = 0;
    const result = await (0, parser_js_1.waitForParsedAdsRows)({}, {
        timeoutMs: 60,
        pollMs: 5,
        maxPartialRatio: 0.1,
        readRows: async () => {
            attempts += 1;
            // Чередуем "плохой" (100%) и "получше" (60%) — best-so-far должен быть с 60%.
            if (attempts % 2 === 1) {
                return {
                    rows: [makeRow({ fb_ad_id: '1' }), makeRow({ fb_ad_id: '2' })],
                    partialRowIds: ['1', '2'],
                };
            }
            return {
                rows: [makeRow({ fb_ad_id: '1' }), makeRow({ fb_ad_id: '2' })],
                partialRowIds: ['1'],
            };
        },
    });
    strict_1.default.equal(result.rows.length, 2);
    // Best-so-far — это вариант с 1 partial, а не 2.
    strict_1.default.equal(result.partialRowIds.length, 1);
});
//# sourceMappingURL=parser-retry.test.js.map