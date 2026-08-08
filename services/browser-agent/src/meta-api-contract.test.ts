import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import * as path from 'node:path';

describe('MetaApiService v5 transport contract', () => {
  it('publishes only the versioned Graph RPC path', () => {
    const protoPath = path.resolve(__dirname, '../../../proto/v1/meta_api.proto');
    const packageDefinition = protoLoader.loadSync(protoPath, {
      keepCase: true,
      longs: String,
      enums: String,
      defaults: true,
      oneofs: true,
    });
    const loaded = grpc.loadPackageDefinition(packageDefinition) as any;
    const service = loaded.fb_agent.meta_api.v1.MetaApiService.service;

    assert.equal(service.ExecuteGraphCall, undefined);
    assert.equal(
      service.ExecuteGraphCallV5.path,
      '/fb_agent.meta_api.v1.MetaApiService/ExecuteGraphCallV5',
    );
    assert.equal(service.ExecuteGraphCallV5.originalName, 'executeGraphCallV5');
  });
});
