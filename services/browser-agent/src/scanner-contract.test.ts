import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';

import * as protoLoader from '@grpc/proto-loader';

import { METRICS_CONTRACT_REVISION } from './am/am-completeness.js';

test('scanner wire contract exposes the fail-closed metrics revision', () => {
  const protoPath = path.resolve(__dirname, '../../../proto/v1/scanner.proto');
  const definition = protoLoader.loadSync(protoPath, {
    keepCase: true,
    longs: String,
    enums: String,
    defaults: true,
    oneofs: true,
  });
  const scanComplete = definition[
    'fb_agent.scanner.v1.ScanComplete'
  ] as protoLoader.MessageTypeDefinition;
  const messageType = scanComplete.type as {
    field: Array<{ name: string; number: number; type: string }>;
  };
  const revisionField = messageType.field.find(
    (field) => field.name === 'metrics_contract_revision',
  );

  assert.equal(METRICS_CONTRACT_REVISION, 1);
  assert.equal(revisionField?.number, 11);
  assert.equal(revisionField?.type, 'TYPE_UINT32');

  const scannerService = definition[
    'fb_agent.scanner.v1.ScannerService'
  ] as unknown as {
    RunScanCycle: {
      responseSerialize(value: unknown): Buffer;
      responseDeserialize(value: Buffer): {
        complete?: { metrics_contract_revision?: number };
      };
    };
  };
  const wire = scannerService.RunScanCycle.responseSerialize({
    session_id: 'contract-test',
    complete: { metrics_contract_revision: METRICS_CONTRACT_REVISION },
  });
  const decoded = scannerService.RunScanCycle.responseDeserialize(wire);

  assert.equal(
    decoded.complete?.metrics_contract_revision,
    METRICS_CONTRACT_REVISION,
  );
});
