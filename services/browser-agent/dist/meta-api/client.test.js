"use strict";
// H-8 (BA-3): executeGraphCall error-mapping. Канал мутаций (pause_ad/activate_ad/
// budget/create) опирается на классификацию error.code (190 → токен протух,
// -1/-2/-3 → token/network/page-evaluate). Регресс на разбор error-блока Meta.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const client_js_1 = require("./client.js");
// Мок Playwright Page: page.evaluate(fn, args) → вызываем evalImpl(args) (fn игнорируем).
function mockPage(evalImpl) {
    return {
        evaluate: async (_fn, args) => evalImpl(args),
    };
}
const baseParams = { method: 'GET', endpoint: '/me', queryParams: {} };
(0, node_test_1.describe)('executeGraphCall error-mapping (H-8)', () => {
    (0, node_test_1.it)('успешный ответ → error undefined, statusCode/responseJson проброшены', async () => {
        const page = mockPage(() => ({ status_code: 200, response_json: '{"id":"act_1"}' }));
        const r = await (0, client_js_1.executeGraphCall)(page, baseParams);
        strict_1.default.equal(r.statusCode, 200);
        strict_1.default.equal(r.error, undefined);
        strict_1.default.equal(r.responseJson, '{"id":"act_1"}');
        strict_1.default.ok(r.durationMs >= 0);
    });
    (0, node_test_1.it)('Meta error 190 (OAuth, протухший токен) → code/subcode/type/fbtrace разобраны', async () => {
        const body = JSON.stringify({
            error: {
                code: 190,
                error_subcode: 460,
                type: 'OAuthException',
                message: 'Error validating access token',
                fbtrace_id: 'TRACE1',
            },
        });
        const page = mockPage(() => ({ status_code: 400, response_json: body }));
        const r = await (0, client_js_1.executeGraphCall)(page, baseParams);
        strict_1.default.equal(r.statusCode, 400);
        strict_1.default.equal(r.error?.code, 190);
        strict_1.default.equal(r.error?.subcode, 460);
        strict_1.default.equal(r.error?.type, 'OAuthException');
        strict_1.default.equal(r.error?.fbtraceId, 'TRACE1');
    });
    (0, node_test_1.it)('token not found (code -1) пробрасывается из page.evaluate', async () => {
        const body = JSON.stringify({
            error: { code: -1, type: 'TokenNotFound', message: 'EAA-токен не найден' },
        });
        const page = mockPage(() => ({ status_code: 0, response_json: body }));
        const r = await (0, client_js_1.executeGraphCall)(page, baseParams);
        strict_1.default.equal(r.error?.code, -1);
        strict_1.default.equal(r.error?.type, 'TokenNotFound');
    });
    (0, node_test_1.it)('network error (code -2) пробрасывается', async () => {
        const body = JSON.stringify({
            error: { code: -2, type: 'NetworkError', message: 'Timeout после 30000мс' },
        });
        const page = mockPage(() => ({ status_code: 0, response_json: body }));
        const r = await (0, client_js_1.executeGraphCall)(page, baseParams);
        strict_1.default.equal(r.error?.code, -2);
        strict_1.default.equal(r.error?.type, 'NetworkError');
    });
    (0, node_test_1.it)('page.evaluate бросает (context destroyed) → code -3 PageEvaluateError', async () => {
        const page = mockPage(() => {
            throw new Error('Execution context was destroyed');
        });
        const r = await (0, client_js_1.executeGraphCall)(page, {
            method: 'POST',
            endpoint: '/act_1/ads',
            queryParams: {},
        });
        strict_1.default.equal(r.statusCode, 0);
        strict_1.default.equal(r.error?.code, -3);
        strict_1.default.equal(r.error?.type, 'PageEvaluateError');
        strict_1.default.match(r.error?.message ?? '', /context was destroyed/);
    });
    (0, node_test_1.it)('success с массивом data → без error', async () => {
        const page = mockPage(() => ({ status_code: 200, response_json: '{"data":[]}' }));
        const r = await (0, client_js_1.executeGraphCall)(page, {
            method: 'GET',
            endpoint: '/act_1/campaigns',
            queryParams: {},
        });
        strict_1.default.equal(r.error, undefined);
    });
});
//# sourceMappingURL=client.test.js.map