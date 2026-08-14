/**
 * Design tokens — ЕДИНЫЙ ИСТОЧНИК для веба и mini app.
 * Соответствует актуальному контракту в docs/frontend_design.md.
 *
 * CSS-зеркало — ./tokens.css (@theme для Tailwind 4). Инвариант-тест проверяет,
 * что весь public namespace совпадает с :root и Tailwind theme.
 *
 * Dark-only. Острые углы (radius 0 по умолчанию). Accent — warm off-white.
 */

export const colors = {
  // Surfaces — graphite scale (12 ступеней)
  bg0: "#0b0d10",
  bg1: "#0e1114",
  bg2: "#14181b",
  bg3: "#1a1f22",
  bg4: "#202629",
  bg5: "#292f32",
  bg6: "#34383a",
  bg7: "#545a5d",
  bg8: "#858a8d",
  bg9: "#a09f9a",
  bg10: "#c3beb4",
  bg11: "#e7e1d5",

  // Accent — warm off-white (общий emphasis)
  accent: "#b8a36a",
  accentMuted: "#9f8e5e",
  accentBg: "#1d1a13",

  // Active — тёплый амбер (пульс/активная строка, вариант A)
  active: "#b8a36a",
  activeBg: "#211c11",

  // Semantic — яркое кодирование статусов (вариант C).
  // Статусный цвет обязан отличаться и от декора, и от соседнего статуса:
  // warning не повторяет accent/active, danger ярче unknown (яркость идёт по
  // важности) и проходит WCAG AA на bg-4/bg-5, info не сливается с success.
  success: "#86a77b",
  successBg: "#121a13",
  warning: "#d4a858",
  warningBg: "#211c11",
  danger: "#e58177",
  dangerBg: "#281513",
  info: "#7f95a6",
  infoBg: "#151b1f",
  hairline: "#292f32",
  hairlineStrong: "#34383a",
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
  display: '"Commissioner", "SF Pro Display", system-ui, sans-serif',
  body: '"Commissioner", "SF Pro Text", system-ui, sans-serif',
  numeric: '"JetBrains Mono", tabular-nums, monospace',
} as const;

/** Размерная шкала текста (px), §1.2.2. */
export const fontSizes = {
  display: 48,
  title1: 36,
  title2: 22,
  title3: 16,
  body: 16,
  bodySm: 14,
  caption: 12,
  micro: 12,
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
  1: 2,
  2: 4,
  3: 6,
  4: 8,
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

/** Responsive shell layout (§1.8). */
export const layout = {
  sidebarWidth: 196,
  sidebarWidthCollapsed: 64,
  topbarHeight: 56,
  contentPaddingX: 36,
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
