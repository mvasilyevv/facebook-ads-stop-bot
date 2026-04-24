import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveToggleHandleFromCell } from './toggle-utils.js';

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
    $: async (selector: string) => (selector === '[role="switch"]' ? nestedSwitch : null),
  };

  assert.equal(await resolveToggleHandleFromCell(cell), nestedSwitch);
});
