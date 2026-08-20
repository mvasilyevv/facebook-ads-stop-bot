import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import * as grpc from '@grpc/grpc-js';

import type { SessionManager } from '../session-manager.js';
import {
  BROWSER_OPERATION_REJECTION_METADATA_KEY,
  browserOperationRejectionReason,
  createMetaApiServiceHandlers,
  grpcCodeForError,
  grpcErrorForOperationFailure,
} from './service.js';

// Реальные тексты предикатов — ровно те, что рождаются в operation-capability.ts,
// ownership.ts и operation-authority-client.ts. Тест держит соответствие
// «предикат → код причины», а не пересказывает реализацию своими словами.
const PERMISSION_DENIED_PREDICATES: ReadonlyArray<readonly [string, string]> = [
  [
    'Browser operation capability authority is unavailable',
    'capability_authority_unavailable',
  ],
  [
    'Browser operation capability contract is incompatible',
    'capability_contract_incompatible',
  ],
  [
    'Browser operation capability secret is unavailable',
    'capability_secret_unavailable',
  ],
  [
    'Browser operation capability cabinet binding is invalid',
    'capability_cabinet_mismatch',
  ],
  ['Browser operation caller is not authorized', 'caller_not_authorized'],
  ['Browser operation task binding is invalid', 'capability_task_binding_invalid'],
  [
    'Browser operation lease binding is invalid',
    'capability_lease_binding_invalid',
  ],
  ['Browser operation capability is expired or unbounded', 'capability_expired'],
  ['Browser operation capability is malformed', 'capability_malformed'],
  [
    'Browser operation capability signature is invalid',
    'capability_signature_invalid',
  ],
  ['Browser operation capability is invalid', 'capability_invalid'],
  [
    'Browser operation ownership preflight rejected the Graph target',
    'ownership_preflight_rejected',
  ],
  ['Graph method override is not authorized', 'graph_method_override'],
  ['Graph request method semantics are ambiguous', 'graph_method_semantics'],
  ['Graph GET body semantics are not authorized', 'graph_get_body'],
  [
    'Graph endpoint query/fragment semantics are not authorized',
    'graph_endpoint_query',
  ],
];

// Отказы по ad_account_id: аргумент запроса, а не права вызывающего. Тексты —
// ровно те, что отдаёт executeGraphCallV5Handler до выбора страницы.
const ARGUMENT_REJECTION_PREDICATES: ReadonlyArray<readonly [string, string]> = [
  [
    'ad_account_id must be an explicit numeric account id',
    'ad_account_id_not_numeric',
  ],
  [
    'money Graph call requires explicit ad_account_id',
    'ad_account_id_missing',
  ],
];

function trailerReason(error: {
  metadata?: grpc.Metadata;
}): string | undefined {
  const values = error.metadata?.get(BROWSER_OPERATION_REJECTION_METADATA_KEY);
  return values && values.length > 0 ? String(values[0]) : undefined;
}

describe('browser operation authorization rejection names its reason', () => {
  it('gives every PERMISSION_DENIED predicate its own reason code', () => {
    const seen = new Set<string>();
    for (const [message, expectedReason] of PERMISSION_DENIED_PREDICATES) {
      const err = new Error(message);
      assert.equal(
        grpcCodeForError(err),
        grpc.status.PERMISSION_DENIED,
        `${message} must stay PERMISSION_DENIED`,
      );
      assert.equal(
        browserOperationRejectionReason(err),
        expectedReason,
        `${message} must report ${expectedReason}`,
      );
      seen.add(expectedReason);
    }
    assert.equal(
      seen.size,
      PERMISSION_DENIED_PREDICATES.length,
      'reason codes must be distinct, not collapsed into one string',
    );
  });

  it('carries the reason code in a gRPC trailer, not inside details', () => {
    for (const [message, expectedReason] of PERMISSION_DENIED_PREDICATES) {
      const error = grpcErrorForOperationFailure(new Error(message));
      assert.equal(error.code, grpc.status.PERMISSION_DENIED);
      assert.equal(trailerReason(error), expectedReason);
    }
  });

  it('leaves an aborted capability grant reconcilable, not rejected', () => {
    // Грант мог быть списан процессом, который умер после пересечения границы:
    // это ABORTED и ручная сверка, а не доказанный отказ до отправки.
    const err = new Error('Browser operation capability consume was denied');
    assert.equal(grpcCodeForError(err), grpc.status.ABORTED);
    assert.equal(browserOperationRejectionReason(err), undefined);
    assert.equal(trailerReason(grpcErrorForOperationFailure(err)), undefined);
  });

  it('answers both ad_account_id refusals with the reason in the trailer', async () => {
    const requests: ReadonlyArray<readonly [Record<string, unknown>, string]> = [
      [
        { method: 'GET', endpoint: '/me', ad_account_id: 'act_not_a_number' },
        'ad_account_id_not_numeric',
      ],
      [
        {
          method: 'POST',
          endpoint: '/me',
          body_json: '{}',
          ad_account_id: '',
          authorized_caller: 'campaign_creator',
        },
        'ad_account_id_missing',
      ],
    ];
    let touchedBrowser = 0;
    const manager = {
      getSession: () => { touchedBrowser += 1; return { id: 's', visionProfileId: 'p' }; },
      getPreferredSession: () => {
        touchedBrowser += 1;
        return { id: 's', visionProfileId: 'p' };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getControlPage: () => { touchedBrowser += 1; return {} as any; },
      getInteractivePage: () => { touchedBrowser += 1; return {} as any; },
      verifyOperationCapability: () => { touchedBrowser += 1; },
      assertGraphOperationOwnership: async () => { touchedBrowser += 1; },
      consumeOperationCapability: async () => { touchedBrowser += 1; },
    });

    for (const [request, expectedReason] of requests) {
      const call = new EventEmitter() as EventEmitter & {
        request: Record<string, unknown>;
        getDeadline: () => Date;
      };
      call.request = { query_params: {}, timeout_ms: 30_000, ...request };
      call.getDeadline = () => new Date(Date.now() + 30_000);
      const error = await new Promise<any>((resolve) => {
        void handlers.executeGraphCallV5(call, (value: unknown) => resolve(value));
      });

      assert.equal(error.code, grpc.status.INVALID_ARGUMENT);
      assert.equal(trailerReason(error), expectedReason);
    }
    // Ни один отказ не дошёл до сессии, страницы и гранта: код причины
    // одновременно доказывает, что наружу ничего не ушло.
    assert.equal(touchedBrowser, 0);
  });

  it('does not label non-authorization failures with a rejection reason', () => {
    for (const message of [
      'cabinet_login_required: Vision profile is signed out (act=111)',
      'page_epoch_changed',
      'Browser operation requires exact session/profile identity',
      'Browser operation ownership preflight could not read the Graph target',
      'Ads Manager page not found',
    ]) {
      assert.equal(
        browserOperationRejectionReason(new Error(message)),
        undefined,
        `${message} must not claim an authorization reason`,
      );
    }
  });

  it('names the ad_account_id refusals without calling them an authorization failure', () => {
    // Оба отказа рождаются в executeGraphCallV5Handler до выбора страницы,
    // до списания гранта и до первого fetch. Код причины у них есть, но
    // статус остаётся INVALID_ARGUMENT: аргумент собран неверно, прав это
    // не касается, и подмена статуса скрыла бы, что чинить нужно вызывающему.
    for (const [message, expectedReason] of ARGUMENT_REJECTION_PREDICATES) {
      const err = new Error(message);
      assert.equal(
        browserOperationRejectionReason(err),
        expectedReason,
        `${message} must report ${expectedReason}`,
      );
      assert.equal(
        grpcCodeForError(err),
        grpc.status.INVALID_ARGUMENT,
        `${message} must stay INVALID_ARGUMENT`,
      );
      assert.equal(
        trailerReason(grpcErrorForOperationFailure(err)),
        expectedReason,
      );
    }
  });

  it('keeps task and lease binding refusals out of the lost-response path', () => {
    // Обе привязки проверяются до первого fetch. INTERNAL отправлял их в
    // «ответ потерян», то есть в ручную сверку по запросу, которого не было.
    for (const message of [
      'Browser operation task binding is invalid',
      'Browser operation lease binding is invalid',
    ]) {
      assert.equal(
        grpcCodeForError(new Error(message)),
        grpc.status.PERMISSION_DENIED,
        `${message} must not be reported as a lost response`,
      );
    }
  });
});
