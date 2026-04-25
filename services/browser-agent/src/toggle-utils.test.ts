import test from 'node:test';
import assert from 'node:assert/strict';
import { TOGGLE_SELECTOR, resolveToggleHandleFromCell } from './toggle-utils.js';

// Проверяем, что общий селектор не захватывает checkbox выбора строки.
test('TOGGLE_SELECTOR targets only delivery switch elements', () => {
  assert.equal(TOGGLE_SELECTOR, '[role="switch"]');
});

// Проверяем, что helper не теряет toggle, если fallback уже вернул сам switch-элемент.
test('resolveToggleHandleFromCell returns cell itself when it is already a switch', async () => {
  const switchHandle = {
    getAttribute: async (name: string) => (name === 'role' ? 'switch' : null),
    $: async () => null,
  };

  assert.equal(await resolveToggleHandleFromCell(switchHandle), switchHandle);
});

// Проверяем, что helper умеет находить вложенный switch внутри ячейки таблицы.
test('resolveToggleHandleFromCell finds nested switch for regular table cell', async () => {
  const nestedSwitch = {
    getAttribute: async (_name: string) => null,
    $: async () => null,
  };
  const cell = {
    getAttribute: async (_name: string) => null,
    $: async (selector: string) => (selector.includes('[role="switch"]') ? nestedSwitch : null),
  };

  assert.equal(await resolveToggleHandleFromCell(cell), nestedSwitch);
});

// Проверяем, что helper не принимает checkbox выбора строки за toggle объявления.
test('resolveToggleHandleFromCell ignores selection checkbox without switch role', async () => {
  const checkboxHandle = {
    getAttribute: async (name: string) => (name === 'type' ? 'checkbox' : null),
    $: async () => null,
  };

  assert.equal(await resolveToggleHandleFromCell(checkboxHandle), null);
});

// Проверяем, что helper не принимает generic aria-checked без switch role.
test('resolveToggleHandleFromCell ignores generic aria-checked without switch role', async () => {
  const ariaHandle = {
    getAttribute: async (name: string) => (name === 'aria-checked' ? 'false' : null),
    $: async () => null,
  };

  assert.equal(await resolveToggleHandleFromCell(ariaHandle), null);
});
