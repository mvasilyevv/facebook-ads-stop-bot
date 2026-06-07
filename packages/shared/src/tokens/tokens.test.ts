/**
 * Token-invariant тест: CSS-переменные (:root) совпадают с TS-значениями.
 * Цель — запретить дрейф tokens.ts ↔ tokens.css.
 *
 * Читаем tokens.css через node:fs, парсим :root, сверяем hex key-by-key
 * с colors и fsmColors из tokens.ts.
 */

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { colors, fsmColors } from "./tokens";

const __filename = fileURLToPath(import.meta.url);
const __dir = dirname(__filename);

// Читаем CSS файл из той же директории
const cssPath = join(__dir, "tokens.css");
const cssContent = readFileSync(cssPath, "utf-8");

/**
 * Парсит CSS-файл и возвращает map CSS-переменная → hex-значение.
 * Игнорирует var()-ссылки (--fsm-*), комментарии, дублированные секции.
 */
function parseCssVariables(css: string): Map<string, string> {
  const result = new Map<string, string>();
  // Ищем строки вида: --var-name: #hexhex;
  const re = /^\s*(--[\w-]+)\s*:\s*(#[0-9a-fA-F]+)\s*;/gm;
  let match: RegExpExecArray | null;
  while ((match = re.exec(css)) !== null) {
    // Первое вхождение имеет приоритет (вдруг файл содержит дубликаты)
    // noUncheckedIndexedAccess: match[1] и match[2] гарантированы regex-группами
    const varName = match[1];
    const varValue = match[2];
    if (varName && varValue && !result.has(varName)) {
      result.set(varName, varValue.toLowerCase());
    }
  }
  return result;
}

const cssVars = parseCssVariables(cssContent);

// Маппинг TS-ключей colors → CSS-переменных
const COLOR_MAPPING: Array<[keyof typeof colors, string]> = [
  ["bg0", "--color-bg-0"],
  ["bg1", "--color-bg-1"],
  ["bg2", "--color-bg-2"],
  ["bg3", "--color-bg-3"],
  ["bg4", "--color-bg-4"],
  ["bg5", "--color-bg-5"],
  ["bg6", "--color-bg-6"],
  ["bg7", "--color-bg-7"],
  ["bg8", "--color-bg-8"],
  ["bg9", "--color-bg-9"],
  ["bg10", "--color-bg-10"],
  ["bg11", "--color-bg-11"],
  ["accent", "--color-accent"],
  ["accentMuted", "--color-accent-muted"],
  ["accentBg", "--color-accent-bg"],
  ["success", "--color-success"],
  ["successBg", "--color-success-bg"],
  ["warning", "--color-warning"],
  ["warningBg", "--color-warning-bg"],
  ["danger", "--color-danger"],
  ["dangerBg", "--color-danger-bg"],
  ["info", "--color-info"],
  ["infoBg", "--color-info-bg"],
];

describe("Token invariant: tokens.ts ↔ tokens.css (colors)", () => {
  // Каждый цвет из TS должен совпадать с hex в CSS
  it.each(COLOR_MAPPING)("colors.%s === CSS %s", (tsKey, cssVar) => {
    const tsValue = colors[tsKey].toLowerCase();
    const cssValue = cssVars.get(cssVar);
    expect(
      cssValue,
      `CSS-переменная ${cssVar} не найдена в tokens.css`,
    ).toBeDefined();
    // cssValue точно defined — проверено выше через toBeDefined()
    expect(
      tsValue,
      `TS colors.${tsKey} (${tsValue}) ≠ CSS ${cssVar} (${String(cssValue)})`,
    ).toBe(cssValue!);
  });
});

describe("Token invariant: fsmColors ссылаются на корректные palette-значения", () => {
  // FSM-цвета — это ссылки на palette, проверяем что значения существуют в colors
  it("fsmColors.normal === colors.bg9", () => {
    expect(fsmColors.normal.toLowerCase()).toBe(colors.bg9.toLowerCase());
  });
  it("fsmColors.warning === colors.warning", () => {
    expect(fsmColors.warning.toLowerCase()).toBe(colors.warning.toLowerCase());
  });
  it("fsmColors.stop === colors.danger", () => {
    expect(fsmColors.stop.toLowerCase()).toBe(colors.danger.toLowerCase());
  });
  it("fsmColors.claimed === colors.info", () => {
    expect(fsmColors.claimed.toLowerCase()).toBe(colors.info.toLowerCase());
  });
  it("fsmColors.disabled === colors.bg8", () => {
    expect(fsmColors.disabled.toLowerCase()).toBe(colors.bg8.toLowerCase());
  });
});

describe("CSS-файл корректно распарсен", () => {
  // Убедимся что парсер нашёл достаточно переменных
  it("найдено ≥20 CSS-переменных с hex-значениями", () => {
    expect(cssVars.size).toBeGreaterThanOrEqual(20);
  });
});
