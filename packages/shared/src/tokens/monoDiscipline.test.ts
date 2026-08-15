import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * В продукте один моноширинный шрифт, и у него есть имя: токен `font-numeric`
 * (JetBrains Mono). Утилита `font-mono` — дефолт Tailwind, то есть совсем
 * другая гарнитура: там, где её ставили, mini app и веб набирали одно и то же
 * разными шрифтами.
 *
 * Правило то же, что и в вебе: моноширинный принадлежит измеренному — числам,
 * деньгам, долям, отметкам времени. Подписи полей, надзаголовки карточек и
 * теги — обычный текст, и моно превращал экран в распечатку лога.
 */
const ROOT = resolve(__dirname, "../../../..");
const SOURCES = ["frontend/src", "frontend-mini/src", "packages/operator-ui/src"];

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      yield* walk(path);
    } else if (/\.(tsx|ts|css)$/.test(entry)) {
      yield path;
    }
  }
}

describe("моноширинный шрифт в продукте один", () => {
  it("нигде не используется чужая утилита font-mono", () => {
    const offenders: string[] = [];

    for (const source of SOURCES) {
      for (const file of walk(resolve(ROOT, source))) {
        const contents = readFileSync(file, "utf8");
        contents.split("\n").forEach((line, index) => {
          if (/\bfont-mono\b/.test(line)) {
            offenders.push(`${file.slice(ROOT.length + 1)}:${index + 1}`);
          }
        });
      }
    }

    expect(offenders, "font-mono — дефолт Tailwind, а не токен продукта").toEqual([]);
  });
});
