// @fb/shared — общий код веб-дашборда и Telegram Mini App.
// Единый контракт: типы API, FSM-константы+лейблы, форматтеры, доменные хелперы, токены.
//
// Phase 0 (фундамент): токены.
// Phase 1A: format/, constants/, domain/, api/types.

// ─── Токены ──────────────────────────────────────────────────────────────────
export * from "./tokens/tokens";

// ─── API-типы ────────────────────────────────────────────────────────────────
export * from "./api/types";

// ─── Константы FSM / правил / мутаций ────────────────────────────────────────
export * from "./constants/states";
export * from "./constants/rules";
export * from "./constants/mutations";

// ─── Форматтеры ──────────────────────────────────────────────────────────────
export * from "./format/number";
export * from "./format/time";
export * from "./format/id";

// ─── Доменные хелперы ────────────────────────────────────────────────────────
export * from "./domain/badge";
export * from "./domain/diff";
export * from "./domain/expiry";
export * from "./domain/geo";
