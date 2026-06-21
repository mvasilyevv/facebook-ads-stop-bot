// Проверка buildHeartbeatPayload: формирует корректный JSON для Redis-ключа
// worker:heartbeat:browser-agent, совместимый с читателем settings_vision.py.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { buildHeartbeatPayload } from './redis-heartbeat.js';
import type { SessionManager } from './session-manager.js';

/** Минимальный mock SessionManager с активной CDP-сессией. */
function makeSessionManagerWithSession(cdpPort: number): Pick<SessionManager, 'getPreferredSession'> {
  return {
    getPreferredSession: () => ({
      id: 'test-session',
      status: 'connected' as const,
      browser: {} as any,
      cdpPort,
      primaryPage: null,
      playwright: null,
      visionApiUrl: '',
      visionXToken: '',
      visionProfileId: 'test-profile',
      visionFolderId: 'test-folder',
      humanProfile: {
        speedFactor: 1,
        jitterFactor: 0,
        pauseFactor: 0,
        overshootChance: 0,
        idleChance: 0,
        idleDurationMin: 0,
        idleDurationMax: 0,
        bezierStepsMin: 5,
        bezierStepsMax: 10,
      },
      connectedAt: new Date(),
    }),
  } as unknown as Pick<SessionManager, 'getPreferredSession'>;
}

/** Mock SessionManager без активных сессий. */
function makeEmptySessionManager(): Pick<SessionManager, 'getPreferredSession'> {
  return {
    getPreferredSession: () => {
      throw new Error('Активная browser-agent сессия не найдена');
    },
  } as unknown as Pick<SessionManager, 'getPreferredSession'>;
}

describe('buildHeartbeatPayload', () => {
  it('возвращает JSON с cdp_ready=true и cdp_port при активной сессии', () => {
    // Активная сессия с CDP-портом 38967 — должны получить cdp_ready=true и правильный порт.
    const manager = makeSessionManagerWithSession(38967);
    const raw = buildHeartbeatPayload(manager as SessionManager);
    const payload = JSON.parse(raw);

    assert.equal(payload.status, 'ONLINE');
    assert.equal(payload.cdp_ready, true);
    assert.equal(payload.cdp_port, 38967);
    assert.equal(typeof payload.ts, 'number');
    assert.ok(payload.ts > 0, 'ts должен быть unix-секундами > 0');
    assert.ok(typeof payload.message === 'string', 'message должен быть строкой');
  });

  it('возвращает JSON с cdp_ready=false и cdp_port=null при отсутствии сессий', () => {
    // Нет активных сессий — cdp_ready=false, cdp_port=null, статус всё равно ONLINE.
    const manager = makeEmptySessionManager();
    const raw = buildHeartbeatPayload(manager as SessionManager);
    const payload = JSON.parse(raw);

    assert.equal(payload.status, 'ONLINE');
    assert.equal(payload.cdp_ready, false);
    assert.equal(payload.cdp_port, null);
    assert.equal(typeof payload.ts, 'number');
  });

  it('возвращает cdp_ready=false если browser=null в сессии', () => {
    // Сессия есть, но CDP-браузер null (disconnected) — cdp_ready должен быть false.
    const manager = {
      getPreferredSession: () => ({
        id: 'disconnected-session',
        status: 'disconnected' as const,
        browser: null,
        cdpPort: 38967,
        primaryPage: null,
        playwright: null,
        visionApiUrl: '',
        visionXToken: '',
        visionProfileId: 'test-profile',
        visionFolderId: 'test-folder',
        humanProfile: {
          speedFactor: 1, jitterFactor: 0, pauseFactor: 0, overshootChance: 0,
          idleChance: 0, idleDurationMin: 0, idleDurationMax: 0,
          bezierStepsMin: 5, bezierStepsMax: 10,
        },
        connectedAt: new Date(),
      }),
    } as unknown as SessionManager;

    const raw = buildHeartbeatPayload(manager);
    const payload = JSON.parse(raw);

    assert.equal(payload.status, 'ONLINE');
    assert.equal(payload.cdp_ready, false);
    assert.equal(payload.cdp_port, null);
  });

  it('поле ts находится в допустимом диапазоне текущего времени', () => {
    // ts должен быть unix-секундами в пределах 5 секунд от now.
    const before = Math.floor(Date.now() / 1000);
    const manager = makeEmptySessionManager();
    const payload = JSON.parse(buildHeartbeatPayload(manager as SessionManager));
    const after = Math.floor(Date.now() / 1000);

    assert.ok(payload.ts >= before && payload.ts <= after + 1,
      `ts=${payload.ts} должен быть между ${before} и ${after + 1}`);
  });
});
