import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Гард: склонение числительных живёт только в packages/shared.
 *
 * Объявление функции, чьё имя содержит `plural` или `russianCount`,
 * вне packages/shared — нарушение дисциплины. Импорт этих имён
 * нарушением не является.
 *
 * Образец: packages/shared/src/tokens/monoDiscipline.test.ts
 */
const ROOT = resolve(__dirname, "../../../../..");
const SOURCES = ["frontend/src", "frontend-mini/src", "packages/operator-ui/src"];

// Ищем объявления функций — function foo / const foo = / function foo(
// Не ищем строки import { ... } — они легальны.
const DECLARATION_RE =
  /(?:^|\s)(?:function|const|let|var)\s+((?:plural|russianCount)\w*)\s*(?:=\s*(?:async\s*)?\(|[(])/;

function* walk(directory: string): Generator<string> {
  let entries: string[];
  try {
    entries = readdirSync(directory);
  } catch {
    // директория может не существовать (packages/operator-ui/src при сборке)
    return;
  }
  for (const entry of entries) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      yield* walk(path);
    } else if (/\.(tsx?|js)$/.test(entry)) {
      yield path;
    }
  }
}

describe("склонение числительных живёт только в packages/shared (#210)", () => {
  it("нигде во фронтах нет объявлений plural*/russianCount*-функций", () => {
    const offenders: string[] = [];

    for (const source of SOURCES) {
      for (const file of walk(resolve(ROOT, source))) {
        const contents = readFileSync(file, "utf8");
        contents.split("\n").forEach((line, index) => {
          if (DECLARATION_RE.test(line)) {
            offenders.push(`${file.slice(ROOT.length + 1)}:${index + 1}: ${line.trim()}`);
          }
        });
      }
    }

    expect(
      offenders,
      "Локальные копии plural*/russianCount* — нарушение: перенеси в packages/shared/src/format/russianCount.ts",
    ).toEqual([]);
  });
});
