import type { components } from "@fb/shared/api/generated";

export type TmaResolvedNavigation =
  components["schemas"]["TmaNavigationResolveResponse"];

type TmaAttentionRoute =
  | "/"
  | "/actions"
  | "/ads"
  | "/incidents"
  | "/analytics"
  | "/settings"
  | "/system/sources";

export type TmaAttentionNavigation =
  | { kind: "target"; target: TmaResolvedNavigation }
  | { kind: "route"; to: TmaAttentionRoute };

const SAFE_ATTENTION_ROUTES: readonly TmaAttentionRoute[] = [
  "/",
  "/actions",
  "/ads",
  "/incidents",
  "/analytics",
  "/settings",
  "/system/sources",
];

const SESSION_KEY = "fb-agent:tma-resolved-navigation";

export type TmaNavigationResolutionState =
  | { status: "idle"; target: null }
  | { status: "resolving"; target: null }
  | { status: "resolved"; target: TmaResolvedNavigation }
  | { status: "error"; target: null };

const IDLE_NAVIGATION_STATE: TmaNavigationResolutionState = {
  status: "idle",
  target: null,
};
let navigationState: TmaNavigationResolutionState = IDLE_NAVIGATION_STATE;
let hydrated = false;
const navigationListeners = new Set<() => void>();

export function storeResolvedNavigation(target: TmaResolvedNavigation): void {
  try {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(target));
  } catch {
    // Telegram WebView may deny storage; the current in-memory navigation still works.
  }
  setNavigationState({ status: "resolved", target });
}

export function readResolvedNavigation(): TmaResolvedNavigation | null {
  return readResolvedNavigationState().target;
}

export function readResolvedNavigationState(): TmaNavigationResolutionState {
  hydrateNavigationState();
  return navigationState;
}

export function subscribeResolvedNavigation(listener: () => void): () => void {
  navigationListeners.add(listener);
  return () => navigationListeners.delete(listener);
}

export function beginResolvedNavigation(): void {
  removePersistedNavigation();
  setNavigationState({ status: "resolving", target: null });
}

export function failResolvedNavigation(): void {
  removePersistedNavigation();
  setNavigationState({ status: "error", target: null });
}

export function clearResolvedNavigation(): void {
  removePersistedNavigation();
  setNavigationState(IDLE_NAVIGATION_STATE);
}

function hydrateNavigationState(): void {
  if (hydrated) return;
  hydrated = true;
  try {
    const value = JSON.parse(
      window.sessionStorage.getItem(SESSION_KEY) ?? "null",
    ) as unknown;
    if (isResolvedNavigation(value)) {
      navigationState = { status: "resolved", target: value };
      return;
    }
  } catch {
    // Invalid or unavailable storage is an explicit missing-link state.
  }
  navigationState = IDLE_NAVIGATION_STATE;
}

function removePersistedNavigation(): void {
  try {
    window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // In-memory state remains authoritative for the current WebView.
  }
}

function setNavigationState(next: TmaNavigationResolutionState): void {
  hydrated = true;
  navigationState = next;
  navigationListeners.forEach((listener) => listener());
}

/**
 * Converts server-provided operator links into the Mini App's own navigation
 * model. Target identifiers are moved to transient storage and never copied
 * into the address bar.
 */
export function parseTmaAttentionHref(
  href: string,
): TmaAttentionNavigation | null {
  if (isSafeAttentionRoute(href)) {
    return { kind: "route", to: href };
  }

  const match = /^\/(ads|actions|incidents)\/([^/?#]+)$/.exec(href);
  if (!match) return null;
  let targetId: string;
  try {
    targetId = decodeURIComponent(match[2] ?? "");
  } catch {
    return null;
  }
  if (!targetId || targetId.length > 160) return null;
  const targetKind =
    match[1] === "ads" ? "ad" : match[1] === "actions" ? "action" : "incident";
  return {
    kind: "target",
    target: { target_kind: targetKind, target_id: targetId },
  };
}

function isSafeAttentionRoute(value: string): value is TmaAttentionRoute {
  return SAFE_ATTENTION_ROUTES.some((route) => route === value);
}

function isResolvedNavigation(value: unknown): value is TmaResolvedNavigation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<TmaResolvedNavigation>;
  return (
    (candidate.target_kind === "ad" ||
      candidate.target_kind === "action" ||
      candidate.target_kind === "incident") &&
    typeof candidate.target_id === "string" &&
    candidate.target_id.length > 0 &&
    candidate.target_id.length <= 160
  );
}
