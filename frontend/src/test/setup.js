import "@testing-library/jest-dom";

// Полифилл localStorage для jsdom-окружения без валидного origin
if (typeof localStorage === 'undefined' || typeof localStorage.getItem !== 'function') {
  const store = {};
  global.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: (key) => { delete store[key]; },
    clear: () => { Object.keys(store).forEach((k) => delete store[k]); },
  };
}
