import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import * as path from 'path';

import { BROWSER_CONTRACT_VERSION } from './service.js';

export type HealthResponse = {
  healthy?: boolean;
  probe_performed?: boolean;
  probe_ok?: boolean;
  browser_contract_version?: number;
  session_id?: string;
  vision_profile_id?: string;
};

export function serializeBrowserHealth(response: HealthResponse): string {
  return `${JSON.stringify(response)}\n`;
}

export function isExactBrowserHealth(
  response: HealthResponse,
  expectedProfileId: string,
): boolean {
  const expected = expectedProfileId.trim();
  return Boolean(
    expected
    && response.healthy === true
    && response.probe_performed === true
    && response.probe_ok === true
    && response.browser_contract_version === BROWSER_CONTRACT_VERSION
    && String(response.session_id || '').trim()
    && String(response.vision_profile_id || '').trim() === expected,
  );
}

function loadMetaApi(): any {
  const protoPath = path.resolve(__dirname, '../../../../proto/v1/meta_api.proto');
  const definition = protoLoader.loadSync(protoPath, {
    keepCase: true,
    longs: String,
    enums: String,
    defaults: true,
    oneofs: true,
  });
  return (grpc.loadPackageDefinition(definition) as any).fb_agent.meta_api.v1.MetaApiService;
}

async function probe(expectedProfileId: string): Promise<HealthResponse> {
  const MetaApiService = loadMetaApi();
  const client = new MetaApiService(
    '127.0.0.1:50051',
    grpc.credentials.createInsecure(),
  );
  try {
    return await new Promise<HealthResponse>((resolve, reject) => {
      client.checkMetaApiHealth(
        {
          session_id: '',
          full_probe: true,
          expected_vision_profile_id: expectedProfileId,
        },
        {
          deadline: Date.now() + 20_000,
        },
        (error: Error | null, value: HealthResponse) => {
          if (error) reject(error);
          else resolve(value);
        },
      );
    });
  } finally {
    client.close();
  }
}

async function main(): Promise<void> {
  const jsonMode = process.argv[2] === '--json';
  const expectedProfileId = String(process.argv[jsonMode ? 3 : 2] || '').trim();
  if (
    !expectedProfileId
    || expectedProfileId.length > 200
    || /[\u0000-\u001f\u007f]/.test(expectedProfileId)
  ) {
    throw new Error('a safe canonical Vision profile id is required');
  }
  const response = await probe(expectedProfileId);
  if (jsonMode) {
    process.stdout.write(serializeBrowserHealth(response));
    return;
  }
  if (!isExactBrowserHealth(response, expectedProfileId)) {
    throw new Error('exact browser Graph health was not confirmed');
  }
  process.stdout.write('Exact browser Graph health confirmed\n');
}

if (require.main === module) {
  main().catch((error: unknown) => {
    process.stderr.write(`ERROR: ${error instanceof Error ? error.message : 'health probe failed'}\n`);
    process.exitCode = 1;
  });
}
