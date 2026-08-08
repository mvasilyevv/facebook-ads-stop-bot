import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  isExactBrowserHealth,
  serializeBrowserHealth,
} from './health-probe-cli.js';

const ready = {
  healthy: true,
  probe_performed: true,
  probe_ok: true,
  browser_contract_version: 5,
  session_id: 'session-1',
  vision_profile_id: 'profile-1',
};

describe('browser-agent exact health CLI verdict', () => {
  it('accepts only complete health for the requested live profile', () => {
    assert.equal(isExactBrowserHealth(ready, 'profile-1'), true);
  });

  for (const patch of [
    { healthy: false },
    { probe_performed: false },
    { probe_ok: false },
    { browser_contract_version: 4 },
    { session_id: '' },
    { vision_profile_id: 'another-profile' },
  ]) {
    it(`rejects ${Object.keys(patch)[0]} mismatch`, () => {
      assert.equal(isExactBrowserHealth({ ...ready, ...patch }, 'profile-1'), false);
    });
  }

  it('emits raw versioned JSON for the external release contract verdict', () => {
    const stale = { ...ready, browser_contract_version: 4 };
    const output = serializeBrowserHealth(stale);

    assert.deepEqual(JSON.parse(output), stale);
    assert.equal(JSON.parse(output).browser_contract_version, 4);
    assert.doesNotMatch(output, /confirmed/i);
  });
});
