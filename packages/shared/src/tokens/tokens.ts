/**
 * Design tokens — ЕДИНЫЙ ИСТОЧНИК для веба и mini app.
 * Соответствует docs/frontend_design.md §1 и handoff-макетам docs/frontend_mockups/.
 *
 * CSS-зеркало — ./tokens.css (@theme для Tailwind 4). Инвариант-тест (Phase 1A)
 * проверяет, что значения здесь побитно совпадают с :root в tokens.css.
 *
 * Dark-only. Острые углы (radius 0 по умолчанию). Accent — warm off-white.
 */

export const colors = {
  // Surfaces — graphite scale (12 ступеней)
  bg0: "#0a0a0b",
  bg1: "#101012",
  bg2: "#16161a",
  bg3: "#1c1c21",
  bg4: "#232329",
  bg5: "#2c2c33",
  bg6: "#38383f",
  bg7: "#4a4a52",
  bg8: "#5c5c66",
  bg9: "#7c7c86",
  bg10: "#a8a8b0",
  bg11: "#e4e4e7",

  // Accent — warm off-white (общий emphasis)
  accent: "#f5f1e8",
  accentMuted: "#bdb8ab",
  accentBg: "#2a2823",

  // Active — тёплый амбер (пульс/активная строка, вариант A)
  active: "#e8b339",
  activeBg: "#2a2412",

  // Semantic — яркое кодирование статусов (вариант C)
  success: "#34d399",
  successBg: "#10241c",
  warning: "#fbbf24",
  warningBg: "#2a2008",
  danger: "#f87171",
  dangerBg: "#2a1414",
  info: "#60a5fa",
  infoBg: "#0f1f33",
} as const;

/** FSM-state цвета, привязанные к alert_state (lowercase canon).
 * Норма — зелёный, Отключено — нейтральный серый (resolved, не алярм). */
export const fsmColors = {
  normal: colors.success,
  warning: colors.warning,
  stop: colors.danger,
  claimed: colors.info,
  disabled: colors.bg8,
} as const;

export const fonts = {
  display: '"JetBrains Mono", "SF Mono", "Menlo", ui-monospace, monospace',
  body: '"Inter Tight", "SF Pro Text", system-ui, sans-serif',
  numeric: '"JetBrains Mono", tabular-nums, monospace',
} as const;

/** Размерная шкала текста (px), §1.2.2. */
export const fontSizes = {
  display: 56,
  title1: 32,
  title2: 22,
  title3: 16,
  body: 14,
  bodySm: 13,
  caption: 12,
  micro: 10,
} as const;

/** Spacing — 4px baseline (§1.3). */
export const space = {
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  8: 32,
  10: 40,
  12: 56,
} as const;

/** Radius — мягкие углы (направление A+C). */
export const radius = {
  0: 0,
  1: 6,
  2: 10,
  3: 14,
  4: 18,
  full: 9999,
} as const;

export const motion = {
  easeOut: "cubic-bezier(0.2, 0.8, 0.2, 1)",
  easeIn: "cubic-bezier(0.4, 0, 1, 1)",
  easeSpring: "cubic-bezier(0.34, 1.56, 0.64, 1)",
  durFast: "120ms",
  durBase: "200ms",
  durSlow: "400ms",
} as const;

/** Layout — viewport ≥1280 (§1.8). */
export const layout = {
  sidebarWidth: 240,
  sidebarWidthCollapsed: 64,
  topbarHeight: 56,
  contentPaddingX: 32,
  contentPaddingY: 24,
} as const;

export const zIndex = {
  base: 0,
  sticky: 10,
  drawer: 50,
  modal: 60,
  toast: 70,
  tooltip: 80,
} as const;

export const tokens = {
  colors,
  fsmColors,
  fonts,
  fontSizes,
  space,
  radius,
  motion,
  layout,
  zIndex,
} as const;

export type Tokens = typeof tokens;
