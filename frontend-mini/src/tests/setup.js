// Глобальный setup для vitest: матчеры jest-dom + рабочий localStorage/sessionStorage.
import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom при opaque-origin не даёт полноценный Storage (нет .clear) — ставим
// простой in-memory полифилл, чтобы auth.js/api.js работали в тестах.
class MemStorage {
  constructor() {
    this._m = new Map();
  }
  getItem(k) {
    return this._m.has(k) ? this._m.get(k) : null;
  }
  setItem(k, v) {
    this._m.set(String(k), String(v));
  }
  removeItem(k) {
    this._m.delete(k);
  }
  clear() {
    this._m.clear();
  }
  key(i) {
    return Array.from(this._m.keys())[i] ?? null;
  }
  get length() {
    return this._m.size;
  }
}

vi.stubGlobal("localStorage", new MemStorage());
vi.stubGlobal("sessionStorage", new MemStorage());

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});
