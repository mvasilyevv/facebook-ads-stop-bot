import type { AnalyticsPerformance } from "../api/types";
import type { DataState } from "../operator/contracts";
import { analyticsWindowSafety } from "./windowSafety";

export function effectiveAnalyticsState(
  state: DataState,
  {
    realtimeConnected,
    placeholder = false,
    refreshing = false,
    windowKnown = true,
  }: {
    realtimeConnected: boolean;
    placeholder?: boolean;
    refreshing?: boolean;
    windowKnown?: boolean;
  },
): DataState {
  if (state === "unavailable") return state;
  if (!realtimeConnected || placeholder || refreshing) return "stale";
  if (state === "ready" && !windowKnown) return "partial";
  return state;
}

export function analyticsPerformanceState(
  data: AnalyticsPerformance,
  options: {
    realtimeConnected: boolean;
    placeholder?: boolean;
    refreshing?: boolean;
  },
): DataState {
  const safety = analyticsWindowSafety(data.window);
  return effectiveAnalyticsState(data.state, {
    ...options,
    windowKnown: safety.state === "ready",
  });
}

export function inheritAnalyticsState(
  ownState: DataState,
  parentState: DataState,
): DataState {
  if (ownState === "unavailable" || parentState === "unavailable") {
    return "unavailable";
  }
  if (ownState === "stale" || parentState === "stale") return "stale";
  if (ownState === "partial" || parentState === "partial") return "partial";
  if (ownState === "empty") return "empty";
  return "ready";
}
