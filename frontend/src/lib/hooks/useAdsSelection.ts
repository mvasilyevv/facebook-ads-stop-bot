/**
 * useAdsSelection — выбор строк + курсор клавиатурной навигации AdsPage.
 *
 * Выделено из routes/ads/index.tsx (god-component >500 строк) — выбор/курсор
 * не относится к money-критичному bulk-disable flow напрямую (сам flow читает
 * `selected`, но не управляет им).
 */
import { useCallback, useEffect, useState } from "react";
import type { AdSnapshot } from "@fb/shared";

export function useAdsSelection(rows: AdSnapshot[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState(-1);

  // Курсор не должен выходить за пределы после фильтрации.
  useEffect(() => {
    setCursor((c) => (c >= rows.length ? rows.length - 1 : c));
  }, [rows.length]);

  const toggleSelect = useCallback((id: string) => {
    setSelected((p) => {
      const n = new Set(p);
      if (n.has(id)) {
        n.delete(id);
      } else {
        n.add(id);
      }
      return n;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const selectAll = useCallback(() => {
    setSelected((prev) => {
      // Если уже все выбраны — снимаем; иначе выбираем все.
      if (prev.size === rows.length && rows.length > 0) return new Set();
      return new Set(rows.map((r) => r.fb_ad_id));
    });
  }, [rows]);

  return { selected, cursor, setCursor, toggleSelect, clearSelection, selectAll };
}
