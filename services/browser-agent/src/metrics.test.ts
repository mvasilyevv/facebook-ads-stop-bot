import assert from 'node:assert/strict';
import { request } from 'node:http';
import { describe, it } from 'node:test';

import { startBrowserAgentMetricsServer } from './metrics.js';

function get(port: number, path: string): Promise<{ body: string; contentType: string; status: number }> {
  return new Promise((resolve, reject) => {
    const req = request(
      { host: '127.0.0.1', method: 'GET', path, port },
      (response) => {
        response.setEncoding('utf8');
        let body = '';
        response.on('data', (chunk) => {
          body += chunk;
        });
        response.on('end', () => {
          resolve({
            body,
            contentType: String(response.headers['content-type'] ?? ''),
            status: response.statusCode ?? 0,
          });
        });
      },
    );
    req.once('error', reject);
    req.end();
  });
}

describe('browser-agent metrics server', () => {
  it('serves the prom-client registry only from /metrics', async () => {
    const metrics = await startBrowserAgentMetricsServer({ host: '127.0.0.1', port: 0 });
    try {
      const response = await get(metrics.port, '/metrics');
      assert.equal(response.status, 200);
      assert.match(response.contentType, /text\/plain/);
      assert.match(response.body, /fb_agent_worker_heartbeat_timestamp_seconds\{[^}]*worker="browser-agent"/);
      assert.match(response.body, /fb_agent_browser_agent_process_cpu_user_seconds_total/);

      const missing = await get(metrics.port, '/');
      assert.equal(missing.status, 404);
    } finally {
      await metrics.close();
    }
  });
});
