// Тесты чистой логики am-fetch: выбор постера видео + retry-on-transient метрик.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickPreferredThumb, retryTransient } from './am-fetch.js';

// Предпочитаем кадр с is_preferred=true (Meta помечает «главный» кадр видео).
test('pickPreferredThumb: берёт is_preferred', () => {
  const uri = pickPreferredThumb([
    { uri: 'https://cdn/a.jpg', is_preferred: false, width: 1280 },
    { uri: 'https://cdn/b.jpg', is_preferred: true, width: 640 },
  ]);
  assert.equal(uri, 'https://cdn/b.jpg');
});

// Без is_preferred — самый широкий кадр (максимальное качество для drawer).
test('pickPreferredThumb: без preferred — самый широкий', () => {
  const uri = pickPreferredThumb([
    { uri: 'https://cdn/small.jpg', width: 320 },
    { uri: 'https://cdn/big.jpg', width: 1280 },
    { uri: 'https://cdn/mid.jpg', width: 720 },
  ]);
  assert.equal(uri, 'https://cdn/big.jpg');
});

// Пустой/некорректный вход — null (best-effort, без падения).
test('pickPreferredThumb: пустой/мусорный вход → null', () => {
  assert.equal(pickPreferredThumb([]), null);
  assert.equal(pickPreferredThumb(undefined), null);
  assert.equal(pickPreferredThumb(null), null);
  assert.equal(pickPreferredThumb('x'), null);
  assert.equal(pickPreferredThumb([{ width: 100 }]), null); // нет uri
});

// retryTransient: успех с первого раза — без повторов и без ожидания.
test('retryTransient: success сразу → 1 вызов', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return { ok: true } as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => !x.ok },
  );
  assert.equal(calls, 1);
  assert.equal(r.ok, true);
});

// retryTransient: транзиент, затем success — останавливается на успехе.
test('retryTransient: transient → success на 2-й попытке', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return (calls < 2 ? { __amError: true } : { ok: true }) as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => Boolean(x.__amError) },
  );
  assert.equal(calls, 2); // initial + 1 retry
  assert.equal(r.ok, true);
});

// retryTransient: всё время транзиент — исчерпали попытки, вернули последний результат.
test('retryTransient: исчерпание → последний transient-результат', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return { __amError: true, n: calls } as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => Boolean(x.__amError) },
  );
  assert.equal(calls, 3); // initial + 2 retries (delaysMs.length=2)
  assert.equal(r.__amError, true);
});
