import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Сжимаемость важнее вкуса: у flex- и grid-элемента min-width по умолчанию
 * auto, поэтому элемент не может стать уже содержимого. Пока на нём нет
 * min-w-0, соседний truncate не срабатывает никогда — длинное имя кампании
 * или ключ брокера выталкивает карточку за экран. Владелец находил это
 * дважды на живом телефоне; тест держит класс дефекта закрытым.
 */

const ROOTS = [
  resolve(__dirname, "../../.."),
  resolve(__dirname, "../../../../frontend-mini"),
];

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist" || entry === "tests") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (full.endsWith(".tsx")) {
      found.push(full);
    }
  }
  return found;
}

/** Значения className в одном атрибуте: только строковые литералы. */
function classNameLiterals(source: string): string[] {
  return [...source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)].map(
    (match) => match[1] ?? match[2] ?? "",
  );
}

describe("сжимаемость обрезаемых строк", () => {
  it("не оставляет truncate на элементе, который не умеет сжиматься", () => {
    const offenders: string[] = [];

    for (const root of ROOTS) {
      for (const file of sourceFiles(join(root, "src"))) {
        const source = readFileSync(file, "utf8");
        for (const classes of classNameLiterals(source)) {
          const words = classes.split(/\s+/);
          const shrinks = words.includes("flex-1") || words.includes("basis-0");
          const clips = words.includes("truncate");
          if (shrinks && clips && !words.includes("min-w-0")) {
            offenders.push(`${relative(root, file)}: ${classes}`);
          }
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
