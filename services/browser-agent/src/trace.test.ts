import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  traceMetaCall,
  tracePageNav,
  traceSafeEndpoint,
  traceSafeUrl,
} from './trace.js';

function captureLines(run: () => void): string[] {
  const original = console.log;
  const lines: string[] = [];
  console.log = (...args: unknown[]) => {
    lines.push(args.map((value) => String(value)).join(' '));
  };
  try {
    run();
  } finally {
    console.log = original;
  }
  return lines;
}

function parsed(line: string): Record<string, unknown> {
  assert.ok(line.startsWith('[trace] '), `строка следа без префикса: ${line}`);
  return JSON.parse(line.slice('[trace] '.length)) as Record<string, unknown>;
}

const LIVE_TOKEN = `EAA${'B'.repeat(120)}`;

describe('след money-пути: границы', () => {
  it('не выпускает query Graph-вызова наружу', () => {
    const endpoint = traceSafeEndpoint(
      `/act_123/ads?fields=name&access_token=${LIVE_TOKEN}`,
    );
    assert.equal(endpoint, '/act_123/ads');
    assert.ok(!endpoint.includes('access_token'));
    assert.ok(!endpoint.includes(LIVE_TOKEN));
  });

  it('не выпускает токен, оказавшийся в самом пути', () => {
    assert.equal(traceSafeEndpoint(`/me/${LIVE_TOKEN}`), '/me/<token>');
  });

  it('оставляет от адреса страницы только origin и путь', () => {
    assert.equal(
      traceSafeUrl(
        `https://adsmanager.facebook.com/adsmanager/manage/ads?act=123&token=${LIVE_TOKEN}#tab`,
      ),
      'https://adsmanager.facebook.com/adsmanager/manage/ads',
    );
  });

  it('не падает на адресе, который не разбирается', () => {
    assert.equal(traceSafeUrl('about:blank?x=1'), 'about:blank');
  });
});

describe('след money-пути: запись вызова', () => {
  it('пишет одну строку с исходом, кабинетом и страницей', () => {
    const lines = captureLines(() => {
      traceMetaCall({
        rpc: 'execute_graph_call',
        act: '123',
        method: 'POST',
        endpoint: `/act_123/ads?access_token=${LIVE_TOKEN}`,
        money: true,
        session: 'session-1',
        role: 'control',
        outcome: 'CONFIRMED',
        durationMs: 42,
        statusCode: 200,
      });
    });

    assert.equal(lines.length, 1);
    const record = parsed(lines[0]!);
    assert.equal(record.evt, 'meta_call');
    assert.equal(record.act, '123');
    assert.equal(record.method, 'POST');
    assert.equal(record.endpoint, '/act_123/ads');
    assert.equal(record.money, true);
    assert.equal(record.session, 'session-1');
    assert.equal(record.role, 'control');
    assert.equal(record.outcome, 'CONFIRMED');
    assert.equal(record.duration_ms, 42);
    assert.equal(record.status_code, 200);
    assert.ok(typeof record.ts === 'string');
    assert.ok(!lines[0]!.includes(LIVE_TOKEN));
  });

  it('не пишет ключи, которых нет: отсутствие ответа не превращается в ноль', () => {
    const lines = captureLines(() => {
      traceMetaCall({
        rpc: 'execute_graph_call',
        act: '123',
        method: 'POST',
        endpoint: '/act_123/ads',
        money: true,
        session: 'session-1',
        role: 'control',
        outcome: 'UNKNOWN',
        durationMs: 7,
      });
    });

    const record = parsed(lines[0]!);
    assert.ok(!('status_code' in record), 'нет ответа — нет и кода ответа');
    assert.ok(!('reason' in record));
  });
});

describe('след money-пути: запись навигации', () => {
  it('пишет, кто увёл страницу кабинета и куда', () => {
    const lines = captureLines(() => {
      tracePageNav({
        session: 'session-1',
        role: 'control',
        act: '123',
        kind: 'reload',
        url: 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=123',
        by: 'scan',
      });
    });

    const record = parsed(lines[0]!);
    assert.equal(record.evt, 'page_nav');
    assert.equal(record.kind, 'reload');
    assert.equal(record.by, 'scan');
    assert.equal(record.act, '123');
    assert.equal(
      record.url,
      'https://adsmanager.facebook.com/adsmanager/manage/ads',
    );
  });
});
