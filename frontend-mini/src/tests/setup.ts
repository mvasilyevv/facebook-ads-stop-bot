import "@testing-library/jest-dom/vitest";

// jsdom не реализует localStorage/sessionStorage.clear() через стандартный Web Storage.
// Добавляем полифил чтобы auth.test.ts мог вызывать .clear() в beforeEach.
const makeStorageMock = () => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = String(value); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
};

Object.defineProperty(globalThis, "localStorage", {
  value: makeStorageMock(),
  writable: true,
});

Object.defineProperty(globalThis, "sessionStorage", {
  value: makeStorageMock(),
  writable: true,
});
