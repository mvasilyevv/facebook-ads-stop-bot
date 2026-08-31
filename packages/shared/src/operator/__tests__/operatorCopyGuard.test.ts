import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Копия про «unknown ≠ success» и про queued-хвост money-команды живёт одним
 * источником в `actionLabels.ts` (issue-аудит UI, пункт про копипасту
 * формулировок). Раньше эти предложения набирались вручную в
 * `routes/actions/$actionId.tsx` (web), `routes/actions/ActionDetailView.tsx`
 * (TMA), `routes/actions/index.tsx` (web) и в `OperatorAds.tsx` на обоих
 * фронтах — с небольшими текстовыми расхождениями между копиями.
 *
 * Этот гард ловит рецидив: сырой текст баннера/подписи/подтверждения не
 * должен появляться в исходниках фронтов иначе как через импортированную
 * константу.
 */
const ROOT = resolve(__dirname, "../../../../..");
const SOURCES = ["frontend/src", "frontend-mini/src"];

const GUARDED_PHRASES = [
  "Внешний результат неоднозначен",
  "Неизвестный результат означает проверку фактического результата",
  "Результат будет подтверждён отдельной задачей",
];

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      yield* walk(path);
    } else if (/\.(tsx|ts)$/.test(entry) && !/\.test\.(tsx|ts)$/.test(entry)) {
      yield path;
    }
  }
}

describe("операторские формулировки про неизвестный исход — один источник", () => {
  it("не переиспользуются как литеральный текст вне packages/shared", () => {
    const offenders: string[] = [];

    for (const source of SOURCES) {
      for (const file of walk(resolve(ROOT, source))) {
        const contents = readFileSync(file, "utf8");
        contents.split("\n").forEach((line, index) => {
          for (const phrase of GUARDED_PHRASES) {
            if (line.includes(phrase)) {
              offenders.push(`${file.slice(ROOT.length + 1)}:${index + 1} — "${phrase}"`);
            }
          }
        });
      }
    }

    expect(
      offenders,
      "используйте OPERATOR_UNKNOWN_RESULT_NOTICE / OPERATOR_UNKNOWN_RESULT_LIST_NOTICE / " +
        "OPERATOR_COMMAND_QUEUED_NOTICE из @fb/shared/operator/actionLabels вместо копии текста",
    ).toEqual([]);
  });
});
