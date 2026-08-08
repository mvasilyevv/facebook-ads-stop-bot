/**
 * Mobile bottom nav is 56px high. Keep 12px between the nav, trigger and chat
 * panel, then add the device safe-area inset. `md:*` preserves desktop layout.
 */
export const ASSISTANT_TRIGGER_POSITION =
  "fixed right-3 bottom-[calc(68px_+_env(safe-area-inset-bottom,0px))] md:right-6 md:bottom-6";

export const ASSISTANT_PANEL_POSITION =
  "fixed right-3 bottom-[calc(128px_+_env(safe-area-inset-bottom,0px))] md:right-6 md:bottom-20";

export const ASSISTANT_PANEL_WIDTH = "w-[380px] max-w-[calc(100vw-1.5rem)]";

export const ASSISTANT_PANEL_HEIGHT =
  "h-[min(560px,calc(100dvh_-_140px_-_env(safe-area-inset-bottom,0px)))] md:h-[min(560px,70vh)]";
