import "@testing-library/jest-dom/vitest";

// jsdom в vitest не инициализирует localStorage без --localstorage-file, поэтому
// глобальный localStorage = undefined, и zustand persist падает на setItem.
// Подкладываем минимальный in-memory stub (нужен для persist-store вроде useUiStore).
if (
  typeof globalThis.localStorage === "undefined" ||
  typeof globalThis.localStorage.setItem !== "function"
) {
  const store = new Map<string, string>();
  const localStorageStub: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: localStorageStub,
    configurable: true,
    writable: true,
  });
}
