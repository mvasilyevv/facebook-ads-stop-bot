import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

import { collectDefaultMetrics, Gauge, Registry } from 'prom-client';

const DEFAULT_METRICS_PORT = 9464;
const HEARTBEAT_INTERVAL_MS = 20_000;

export interface BrowserAgentMetricsServer {
  readonly port: number;
  readonly registry: Registry;
  close(): Promise<void>;
}

interface MetricsServerOptions {
  host?: string;
  port?: number;
  registry?: Registry;
  heartbeatIntervalMs?: number;
}

export function createBrowserAgentMetricsRegistry(): {
  registry: Registry;
  heartbeat: Gauge<'worker'>;
} {
  const registry = new Registry();
  registry.setDefaultLabels({ service: 'browser-agent' });
  collectDefaultMetrics({ register: registry, prefix: 'fb_agent_browser_agent_' });
  const heartbeat = new Gauge({
    name: 'fb_agent_worker_heartbeat_timestamp_seconds',
    help: 'Unix timestamp of the latest in-process worker heartbeat',
    labelNames: ['worker'] as const,
    registers: [registry],
  });
  heartbeat.labels('browser-agent').setToCurrentTime();
  return { registry, heartbeat };
}

export async function startBrowserAgentMetricsServer(
  options: MetricsServerOptions = {},
): Promise<BrowserAgentMetricsServer> {
  const host = options.host ?? '0.0.0.0';
  const configuredPort = options.port ?? Number(process.env.WORKER_METRICS_PORT ?? DEFAULT_METRICS_PORT);
  if (!Number.isInteger(configuredPort) || configuredPort < 0 || configuredPort > 65_535) {
    throw new Error(`Invalid WORKER_METRICS_PORT: ${configuredPort}`);
  }

  const metrics = options.registry
    ? { registry: options.registry, heartbeat: undefined }
    : createBrowserAgentMetricsRegistry();
  const heartbeatTimer = metrics.heartbeat
    ? setInterval(
        () => metrics.heartbeat?.labels('browser-agent').setToCurrentTime(),
        options.heartbeatIntervalMs ?? HEARTBEAT_INTERVAL_MS,
      )
    : undefined;
  heartbeatTimer?.unref();

  const server: Server = createServer(async (request, response) => {
    const isMetricsRequest =
      request.method === 'GET'
      && (request.url === '/metrics' || request.url?.startsWith('/metrics?'));
    if (!isMetricsRequest) {
      response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end('Not found\n');
      return;
    }

    try {
      const body = await metrics.registry.metrics();
      response.writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Type': metrics.registry.contentType,
      });
      response.end(body);
    } catch (error) {
      console.error('Failed to render browser-agent metrics', error);
      response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end('Metrics unavailable\n');
    }
  });

  try {
    await new Promise<void>((resolve, reject) => {
      const onError = (error: Error) => {
        server.off('listening', onListening);
        reject(error);
      };
      const onListening = () => {
        server.off('error', onError);
        resolve();
      };
      server.once('error', onError);
      server.once('listening', onListening);
      server.listen(configuredPort, host);
    });
  } catch (error) {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    throw error;
  }

  const address = server.address() as AddressInfo;
  return {
    port: address.port,
    registry: metrics.registry,
    close: async () => {
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      if (!server.listening) return;
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}
