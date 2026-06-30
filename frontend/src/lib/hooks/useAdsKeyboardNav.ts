/**
 * useAdsKeyboardNav — keyboard-shortcuts AdsPage: «/» поиск · J/K|↑/↓ курсор ·
 * X выбор · Enter drawer · D disable выбранных · Esc закрыть/сбросить/blur.
 *
 * Выделено из routes/ads/index.tsx (god-component >500 строк) — чистый side-effect,
 * не относится к money-критичному bulk-disable flow (только триггерит confirm-диалог).
 */
import { useEffect, type RefObject } from "react";
import type { AdSnapshot } from "@fb/shared";

interface UseAdsKeyboardNavArgs {
  rows: AdSnapshot[];
  cursor: number;
  setCursor: (updater: (c: number) => number) => void;
  selectedSize: number;
  drawerOpen: boolean;
  searchRef: RefObject<HTMLInputElement | null>;
  scrollRef: RefObject<HTMLDivElement | null>;
  rowHeight: number;
  toggleSelect: (id: string) => void;
  openDrawer: (ad: AdSnapshot) => void;
  clearSelection: () => void;
  requestDisableConfirm: () => void;
}

export function useAdsKeyboardNav({
  rows,
  cursor,
  setCursor,
  selectedSize,
  drawerOpen,
  searchRef,
  scrollRef,
  rowHeight,
  toggleSelect,
  openDrawer,
  clearSelection,
  requestDisableConfirm,
}: UseAdsKeyboardNavArgs) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      // В инпуте — только Esc (blur), остальное не перехватываем.
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
        if (e.key === "Escape") (target as HTMLInputElement).blur();
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => Math.min(rows.length - 1, c + 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => Math.max(0, c - 1));
      } else if (e.key === "x" && cursor >= 0 && rows[cursor]) {
        e.preventDefault();
        toggleSelect(rows[cursor]!.fb_ad_id);
      } else if (e.key === "Enter" && cursor >= 0 && rows[cursor]) {
        e.preventDefault();
        openDrawer(rows[cursor]!);
      } else if (e.key === "d" && selectedSize > 0) {
        e.preventDefault();
        requestDisableConfirm();
      } else if (e.key === "Escape") {
        // Приоритет: drawer (закроется сам через Radix) → иначе сброс выбора.
        if (!drawerOpen && selectedSize > 0) clearSelection();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    rows,
    cursor,
    setCursor,
    selectedSize,
    drawerOpen,
    searchRef,
    toggleSelect,
    openDrawer,
    clearSelection,
    requestDisableConfirm,
  ]);

  // Скроллим курсор во вью при навигации J/K.
  useEffect(() => {
    if (cursor < 0 || !scrollRef.current) return;
    const el = scrollRef.current;
    const top = cursor * rowHeight;
    const bottom = top + rowHeight;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (bottom > el.scrollTop + el.clientHeight) el.scrollTop = bottom - el.clientHeight;
  }, [cursor, rowHeight, scrollRef]);
}
