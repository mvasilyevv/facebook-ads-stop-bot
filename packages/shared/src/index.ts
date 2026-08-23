// @fb/shared — общий код веб-дашборда и Telegram Mini App.
// Единый контракт: типы API, FSM-константы+лейблы, форматтеры, доменные хелперы, токены.
//
// Phase 0 (фундамент): токены.
// Phase 1A: format/, constants/, domain/, api/types.

// ─── Токены ──────────────────────────────────────────────────────────────────
export * from "./tokens/tokens";

// ─── API-типы ────────────────────────────────────────────────────────────────
export * from "./api/types";

// ─── Operator console: public contracts and pure view-models ────────────────
export * from "./operator/contracts";
export * from "./operator/chartModel";
export * from "./operator/commandIntent";
export * from "./operator/stopProximity";
export * from "./operator/reloginRecovery";
export * from "./operator/viewModel";

// ─── Константы правил / мутаций ───────────────────────────────────────────────
export * from "./constants/rules";
export * from "./constants/campaignEnums";

// ─── Форматтеры ──────────────────────────────────────────────────────────────
export * from "./format/number";
export * from "./format/time";
export * from "./format/id";
export * from "./format/russianCount";

// ─── Доменные хелперы ────────────────────────────────────────────────────────
export * from "./domain/geo";
export * from "./domain/campaignRunReview";
