"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_events_1 = require("node:events");
const node_test_1 = __importDefault(require("node:test"));
const index_js_1 = require("./index.js");
class MockStatusCall extends node_events_1.EventEmitter {
    request = { session_id: 'session-1' };
    writes = [];
    ended = false;
    write(event) {
        this.writes.push(event);
    }
    end() {
        this.ended = true;
        this.emit('close');
    }
}
// Сценарий: server-stream handler сразу пишет статус из unary request.
(0, node_test_1.default)('StreamSessionStatus читает session_id из call.request и пишет начальный статус', () => {
    const call = new MockStatusCall();
    (0, index_js_1.streamSessionStatusWithLookup)(call, () => ({
        id: 'session-1',
        status: 'connected',
        primaryPage: { url: () => 'https://adsmanager.facebook.com/' },
    }));
    strict_1.default.equal(call.writes.length, 1);
    strict_1.default.equal(call.writes[0].session_id, 'session-1');
    strict_1.default.equal(call.writes[0].status, 'connected');
    strict_1.default.equal(call.writes[0].current_url, 'https://adsmanager.facebook.com/');
    call.emit('close');
});
// Сценарий: если сессия не найдена, handler пишет error event и завершает stream.
(0, node_test_1.default)('StreamSessionStatus завершает stream после ошибки поиска сессии', () => {
    const call = new MockStatusCall();
    (0, index_js_1.streamSessionStatusWithLookup)(call, () => {
        throw new Error('Сессия не найдена');
    });
    strict_1.default.equal(call.writes.length, 1);
    strict_1.default.equal(call.writes[0].session_id, 'session-1');
    strict_1.default.equal(call.writes[0].status, 'error');
    strict_1.default.equal(call.writes[0].detail, 'Сессия не найдена');
    strict_1.default.equal(call.ended, true);
});
//# sourceMappingURL=session-status.test.js.map