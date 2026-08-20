import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { describe, it } from 'node:test';

import { TOKEN_PATTERN_SOURCE } from './client.js';

// Признак живого токена обязан совпадать во всех точках проверки: money-роль и
// проба, признавшие разное, дают либо ложную готовность канала, либо ложный
// разлогин. Единственный источник — TOKEN_PATTERN_SOURCE, но четыре вхождения
// физически не могут на него сослаться: они живут внутри функций, которые
// Playwright сериализует в страницу через page.evaluate, а там модульной
// области нет. Поэтому связь держится не словами в комментарии, а этим гардом.
describe('шаблон EAA-токена', () => {
  // Тест исполняется из dist/, а сверяет ИСХОДНИКИ: литерал живёт в теле
  // функции, которую page.evaluate сериализует, поэтому проверять надо текст,
  // который пишет человек, а не результат компиляции.
  const here = __dirname;
  const sourceDir = [join(here, '..', '..', 'src', 'meta-api'), here].find((candidate) =>
    existsSync(join(candidate, 'client.ts')),
  );
  assert.ok(sourceDir, 'не найден каталог исходников meta-api');
  const files = ['client.ts', 'upload.ts'];
  const literal = /\/EAA\[[^/]*\{100,\}\//g;

  it('во всех точках проверки совпадает с единственным источником', () => {
    const expected = `/${TOKEN_PATTERN_SOURCE}/`;
    let found = 0;
    for (const name of files) {
      const source: string = readFileSync(join(sourceDir, name), 'utf8');
      for (const match of (source.match(literal) ?? []) as string[]) {
        found += 1;
        assert.equal(
          match,
          expected,
          `${name}: литерал ${match} разошёлся с TOKEN_PATTERN_SOURCE`,
        );
      }
    }
    assert.ok(found >= 4, `ожидались все in-page литералы, найдено ${found}`);
  });
});
