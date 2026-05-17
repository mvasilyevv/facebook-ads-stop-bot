import { describe, it } from 'node:test';
import assert from 'node:assert';

describe('creator entrypoint', () => {
  it('installs window.__fbAgent', async () => {
    const win: any = {};
    (globalThis as any).window = win;
    await import('./index.js');
    assert.ok(win.__fbAgent, 'window.__fbAgent должен быть установлен');
    assert.equal(typeof win.__fbAgent.run, 'function');
    assert.equal(typeof win.__fbAgent.startRecording, 'function');
    assert.equal(typeof win.__fbAgent.stopRecording, 'function');
  });

  it('window.__fbAgent.run делегирует в runPlan', async () => {
    const win: any = (globalThis as any).window;
    const result = await win.__fbAgent.run({ schema_version: 1, steps: [] }, {});
    assert.equal(result.ok, true);
  });
});
