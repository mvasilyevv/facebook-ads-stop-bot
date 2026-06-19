// Тесты чистой логики выбора полноразмерного постера видео из video.thumbnails.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickPreferredThumb } from './am-fetch.js';

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
