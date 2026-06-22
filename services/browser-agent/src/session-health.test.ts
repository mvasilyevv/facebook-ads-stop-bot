// Проверка детекта/счётчика авто-исцеления сети Vision (session-health.ts):
// классификация сетевой ошибки, накопление серии сбоев и cooldown между лечениями.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  isNetworkFetchError,
  recordFetchOutcome,
  shouldHealNow,
  HEAL_NET_FAIL_THRESHOLD,
  HEAL_COOLDOWN_MS,
} from './session-health.js';
import type { BrowserSession } from './types.js';

/** Минимальная заглушка сессии — только поля, нужные хелперам. */
function makeSession(): BrowserSession {
  return {
    id: 's1',
    visionApiUrl: '',
    visionXToken: '',
    visionProfileId: '',
    visionFolderId: '',
    cdpPort: 0,
    playwright: null,
    browser: null,
    primaryPage: null,
    humanProfile: {} as any,
    connectedAt: new Date(0),
    status: 'connected',
  };
}

describe('isNetworkFetchError', () => {
  // Сетевой сбой fetch (транспорт мёртв) — должен распознаваться, разный регистр/формулировки.
  it('распознаёт сетевые маркеры', () => {
    assert.equal(isNetworkFetchError('am_tabular:  TypeError: Failed to fetch'), true);
    assert.equal(isNetworkFetchError('NetworkError when attempting to fetch resource'), true);
    assert.equal(isNetworkFetchError('net::ERR_CONNECTION_RESET'), true);
    assert.equal(isNetworkFetchError('Load failed'), true);
  });

  // Graph-ошибка в теле (токен/права) — НЕ сеть: лечится re-sniff'ом, не рестартом профиля.
  it('не считает Graph-ошибку тела сетевой', () => {
    assert.equal(isNetworkFetchError('am_tabular: 190 OAuthException'), false);
    assert.equal(isNetworkFetchError('100 Invalid parameter'), false);
    assert.equal(isNetworkFetchError(null), false);
    assert.equal(isNetworkFetchError(undefined), false);
  });
});

describe('recordFetchOutcome', () => {
  // Успех сбрасывает серию И уровень эскалации (канал ожил — лечить нечего).
  it('успех обнуляет серию и уровень', () => {
    const s = makeSession();
    s.netFailureStreak = 3;
    s.healLevel = 2;
    recordFetchOutcome(s, true);
    assert.equal(s.netFailureStreak, 0);
    assert.equal(s.healLevel, 0);
  });

  // Сбой инкрементирует серию (с undefined-старта тоже корректно).
  it('сбой инкрементирует серию', () => {
    const s = makeSession();
    recordFetchOutcome(s, false);
    recordFetchOutcome(s, false);
    assert.equal(s.netFailureStreak, 2);
  });
});

describe('shouldHealNow', () => {
  // Ниже порога — не лечим (одиночный блип не должен дёргать сессию).
  it('ниже порога — false', () => {
    const s = makeSession();
    s.netFailureStreak = HEAL_NET_FAIL_THRESHOLD - 1;
    assert.equal(shouldHealNow(s, 1_000_000), false);
  });

  // Достигли порога, лечения ещё не было — пора.
  it('на пороге без прошлого лечения — true', () => {
    const s = makeSession();
    s.netFailureStreak = HEAL_NET_FAIL_THRESHOLD;
    assert.equal(shouldHealNow(s, 1_000_000), true);
  });

  // На пороге, но cooldown ещё не вышел — ждём (не лечим каждый цикл).
  it('cooldown не вышел — false', () => {
    const s = makeSession();
    s.netFailureStreak = HEAL_NET_FAIL_THRESHOLD;
    s.lastHealAt = new Date(1_000_000);
    assert.equal(shouldHealNow(s, 1_000_000 + HEAL_COOLDOWN_MS - 1), false);
  });

  // Cooldown вышел — снова можно лечить (эскалация на следующий уровень).
  it('cooldown вышел — true', () => {
    const s = makeSession();
    s.netFailureStreak = HEAL_NET_FAIL_THRESHOLD;
    s.lastHealAt = new Date(1_000_000);
    assert.equal(shouldHealNow(s, 1_000_000 + HEAL_COOLDOWN_MS), true);
  });
});
