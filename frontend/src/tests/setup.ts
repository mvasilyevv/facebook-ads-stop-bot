/**
 * Vitest setup.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Сбрасываем DOM после каждого теста.
afterEach(() => {
  cleanup();
});

// JSDOM не реализует matchMedia — нужно для prefers-reduced-motion.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Заглушка для ResizeObserver (Radix UI и virtualizer его требуют).
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;

// jsdom иногда не предоставляет полноценный localStorage для zustand/persist.
// Подменяем простым in-memory шимом, который реализует все 4 метода.
const inMemoryStore = (() => {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => (map.has(key) ? map.get(key)! : null),
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    clear: () => map.clear(),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    get length() {
      return map.size;
    },
  } satisfies Storage;
})();
Object.defineProperty(window, "localStorage", { value: inMemoryStore, writable: false });
