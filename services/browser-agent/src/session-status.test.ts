import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { streamSessionStatusWithLookup } from './index.js';

class MockStatusCall extends EventEmitter {
  request = { session_id: 'session-1' };
  writes: any[] = [];
  ended = false;

  write(event: any) {
    this.writes.push(event);
  }

  end() {
    this.ended = true;
    this.emit('close');
  }
}

// Сценарий: server-stream handler сразу пишет статус из unary request.
test('StreamSessionStatus читает session_id из call.request и пишет начальный статус', () => {
  const call = new MockStatusCall();

  streamSessionStatusWithLookup(call, () => ({
    id: 'session-1',
    status: 'connected',
    primaryPage: { url: () => 'https://adsmanager.facebook.com/' },
  } as never));

  assert.equal(call.writes.length, 1);
  assert.equal(call.writes[0].session_id, 'session-1');
  assert.equal(call.writes[0].status, 'connected');
  assert.equal(call.writes[0].current_url, 'https://adsmanager.facebook.com/');

  call.emit('close');
});

// Сценарий: если сессия не найдена, handler пишет error event и завершает stream.
test('StreamSessionStatus завершает stream после ошибки поиска сессии', () => {
  const call = new MockStatusCall();

  streamSessionStatusWithLookup(call, () => {
    throw new Error('Сессия не найдена');
  });

  assert.equal(call.writes.length, 1);
  assert.equal(call.writes[0].session_id, 'session-1');
  assert.equal(call.writes[0].status, 'error');
  assert.equal(call.writes[0].detail, 'Сессия не найдена');
  assert.equal(call.ended, true);
});
