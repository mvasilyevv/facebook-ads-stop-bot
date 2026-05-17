import { describe, it } from 'node:test';
import assert from 'node:assert';
import { UploadCreativesStep } from './upload_creatives.js';

// Идемпотентность: совпадает ли число загруженных превью с числом path-ов.
describe('UploadCreativesStep', () => {
  it('isSatisfied при равенстве кол-ва превью и paths', () => {
    const s = new UploadCreativesStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 2 }, { paths: ['a.jpg', 'b.jpg'] }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 0 }, { paths: ['a.jpg'] }),
      false,
    );
  });
});
