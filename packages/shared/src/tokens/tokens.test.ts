/**
 * Token-invariant тест: CSS-переменные (:root) совпадают с TS-значениями.
 * Цель — запретить дрейф tokens.ts ↔ tokens.css.
 *
 * Читаем tokens.css через node:fs и сверяем весь публичный TS namespace,
 * а также Tailwind @theme contract.
 */

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import {
  colors,
  fonts,
  fontSizes,
  fsmColors,
  layout,
  motion,
  radius,
  space,
  zIndex,
} from "./tokens";

const __filename = fileURLToPath(import.meta.url);
const __dir = dirname(__filename);

// Читаем CSS файл из той же директории
const cssPath = join(__dir, "tokens.css");
const cssContent = readFileSync(cssPath, "utf-8");

function parseCssVariables(
  css: string,
  selector: ":root" | "@theme",
): Map<string, string> {
  const selectorMatch =
    selector === ":root"
      ? /^\s*:root\s*\{/m.exec(css)
      : /^\s*@theme\s*\{/m.exec(css);
  const selectorStart = selectorMatch?.index ?? -1;
  const blockStart = selectorStart < 0 ? -1 : css.indexOf("{", selectorStart);
  const blockEnd = css.indexOf("}", blockStart);
  if (selectorStart < 0 || blockStart < 0 || blockEnd < 0) {
    throw new Error(`CSS block ${selector} not found`);
  }
  const block = css.slice(blockStart + 1, blockEnd);
  const result = new Map<string, string>();
  const re = /^\s*(--[\w-]+)\s*:\s*([^;]+)\s*;/gm;
  let match: RegExpExecArray | null;
  while ((match = re.exec(block)) !== null) {
    const varName = match[1];
    const varValue = match[2];
    if (varName && varValue && !result.has(varName)) {
      result.set(varName, varValue.replace(/\s+/g, " ").trim().toLowerCase());
    }
  }
  return result;
}

const cssVars = parseCssVariables(cssContent, ":root");
const themeVars = parseCssVariables(cssContent, "@theme");

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
  ["active", "--color-active"],
  ["activeBg", "--color-active-bg"],
  ["success", "--color-success"],
  ["successBg", "--color-success-bg"],
  ["warning", "--color-warning"],
  ["warningBg", "--color-warning-bg"],
  ["danger", "--color-danger"],
  ["dangerBg", "--color-danger-bg"],
  ["info", "--color-info"],
  ["infoBg", "--color-info-bg"],
  ["hairline", "--color-hairline"],
  ["hairlineStrong", "--color-hairline-strong"],
];

const ROOT_TOKEN_MAPPING = [
  ["fonts.display", "--font-display", fonts.display],
  ["fonts.body", "--font-body", fonts.body],
  ["fonts.numeric", "--font-numeric", fonts.numeric],
  ["fontSizes.display", "--text-display", `${fontSizes.display}px`],
  ["fontSizes.title1", "--text-title-1", `${fontSizes.title1}px`],
  ["fontSizes.title2", "--text-title-2", `${fontSizes.title2}px`],
  ["fontSizes.title3", "--text-title-3", `${fontSizes.title3}px`],
  ["fontSizes.body", "--text-body", `${fontSizes.body}px`],
  ["fontSizes.bodySm", "--text-body-sm", `${fontSizes.bodySm}px`],
  ["fontSizes.caption", "--text-caption", `${fontSizes.caption}px`],
  ["fontSizes.micro", "--text-micro", `${fontSizes.micro}px`],
  ["space.0", "--space-0", `${space[0]}px`],
  ["space.1", "--space-1", `${space[1]}px`],
  ["space.2", "--space-2", `${space[2]}px`],
  ["space.3", "--space-3", `${space[3]}px`],
  ["space.4", "--space-4", `${space[4]}px`],
  ["space.5", "--space-5", `${space[5]}px`],
  ["space.6", "--space-6", `${space[6]}px`],
  ["space.8", "--space-8", `${space[8]}px`],
  ["space.10", "--space-10", `${space[10]}px`],
  ["space.12", "--space-12", `${space[12]}px`],
  ["radius.0", "--radius-0", `${radius[0]}px`],
  ["radius.1", "--radius-1", `${radius[1]}px`],
  ["radius.2", "--radius-2", `${radius[2]}px`],
  ["radius.3", "--radius-3", `${radius[3]}px`],
  ["radius.4", "--radius-4", `${radius[4]}px`],
  ["radius.full", "--radius-full", `${radius.full}px`],
  ["motion.easeOut", "--ease-out", motion.easeOut],
  ["motion.easeIn", "--ease-in", motion.easeIn],
  ["motion.easeSpring", "--ease-spring", motion.easeSpring],
  ["motion.durFast", "--dur-fast", motion.durFast],
  ["motion.durBase", "--dur-base", motion.durBase],
  ["motion.durSlow", "--dur-slow", motion.durSlow],
  ["layout.sidebarWidth", "--sidebar-width", `${layout.sidebarWidth}px`],
  [
    "layout.sidebarWidthCollapsed",
    "--sidebar-width-collapsed",
    `${layout.sidebarWidthCollapsed}px`,
  ],
  ["layout.topbarHeight", "--topbar-height", `${layout.topbarHeight}px`],
  [
    "layout.contentPaddingX",
    "--content-padding-x",
    `${layout.contentPaddingX}px`,
  ],
  [
    "layout.contentPaddingY",
    "--content-padding-y",
    `${layout.contentPaddingY}px`,
  ],
  ["zIndex.base", "--z-base", String(zIndex.base)],
  ["zIndex.sticky", "--z-sticky", String(zIndex.sticky)],
  ["zIndex.drawer", "--z-drawer", String(zIndex.drawer)],
  ["zIndex.modal", "--z-modal", String(zIndex.modal)],
  ["zIndex.toast", "--z-toast", String(zIndex.toast)],
  ["zIndex.tooltip", "--z-tooltip", String(zIndex.tooltip)],
] as const;

const THEME_ROOT_VARIABLES = [
  ...COLOR_MAPPING.map(([, cssVar]) => cssVar),
  "--font-display",
  "--font-body",
  "--font-numeric",
  "--radius-0",
  "--radius-1",
  "--radius-2",
  "--radius-3",
  "--radius-4",
  "--text-display",
  "--text-title-1",
  "--text-title-2",
  "--text-title-3",
  "--text-body",
  "--text-body-sm",
  "--text-caption",
  "--text-micro",
] as const;

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

describe("Token invariant: full public TS namespace ↔ :root", () => {
  it.each(ROOT_TOKEN_MAPPING)("%s === CSS %s", (label, cssVar, expected) => {
    expect(cssVars.get(cssVar), `${label}: missing ${cssVar}`).toBe(
      expected.replace(/\s+/g, " ").trim().toLowerCase(),
    );
  });
});

describe("Tailwind @theme stays aligned with canonical :root tokens", () => {
  it.each(THEME_ROOT_VARIABLES)("%s matches :root", (cssVar) => {
    expect(themeVars.get(cssVar), `@theme is missing ${cssVar}`).toBe(
      cssVars.get(cssVar),
    );
  });

  it.each([
    "--color-fsm-normal",
    "--color-fsm-warning",
    "--color-fsm-stop",
    "--color-fsm-claimed",
    "--color-fsm-disabled",
  ])("%s is exported", (cssVar) => {
    expect(themeVars.has(cssVar)).toBe(true);
  });
});

describe("Token invariant: fsmColors ссылаются на корректные palette-значения", () => {
  // FSM-цвета — это ссылки на palette, проверяем что значения существуют в colors
  it("fsmColors.normal === colors.success", () => {
    expect(fsmColors.normal.toLowerCase()).toBe(colors.success.toLowerCase());
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

function relativeLuminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) =>
      value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
    (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  );
}

describe("muted production text contrast", () => {
  it("keeps 12px bg8 text above WCAG AA on every supported card surface", () => {
    for (const surface of [colors.bg0, colors.bg1, colors.bg2, colors.bg3]) {
      expect(contrastRatio(colors.bg8, surface)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps danger text above WCAG AA on its semantic surface", () => {
    expect(
      contrastRatio(colors.danger, colors.dangerBg),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps danger readable on the raised surfaces it is actually used on", () => {
    // bg4 и bg5 — фон карточек и строк таблиц: прежний danger давал там
    // 4.05:1 и 3.58:1, то есть не проходил WCAG AA.
    for (const surface of [colors.bg4, colors.bg5]) {
      expect(contrastRatio(colors.danger, surface)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

function hueDegrees(hex: string): number {
  const [red, green, blue] = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((value) => Number.parseInt(value, 16) / 255) as [number, number, number];
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  if (max === min) return 0;
  const delta = max - min;
  const hue =
    max === red
      ? ((green - blue) / delta) % 6
      : max === green
        ? (blue - red) / delta + 2
        : (red - green) / delta + 4;
  return ((hue * 60) % 360) + (hue < 0 ? 360 : 0);
}

function hueDistance(first: string, second: string): number {
  const delta = Math.abs(hueDegrees(first) - hueDegrees(second)) % 360;
  return Math.min(delta, 360 - delta);
}

function chroma(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((value) => Number.parseInt(value, 16));
  return Math.max(...channels) - Math.min(...channels);
}

describe("semantic colours stay distinguishable from each other and from decor", () => {
  it("does not reuse the decorative accent hex for warning", () => {
    // Предупреждение, покрашенное в цвет декора, сливается с подсветкой
    // и перестаёт читаться как статус.
    expect(colors.warning).not.toBe(colors.accent);
    expect(colors.warning).not.toBe(colors.active);
  });

  it("orders brightness by importance: danger is never darker than unknown", () => {
    // unknown рисуется цветом bg8. Прежний danger был темнее него, то есть
    // яркость шла обратно важности.
    expect(relativeLuminance(colors.danger)).toBeGreaterThan(
      relativeLuminance(colors.bg8),
    );
  });

  it("keeps info apart from success and from the grey scale", () => {
    expect(hueDistance(colors.info, colors.success)).toBeGreaterThanOrEqual(60);
    expect(chroma(colors.info)).toBeGreaterThan(chroma(colors.bg9));
    expect(chroma(colors.info)).toBeGreaterThanOrEqual(32);
  });
});
