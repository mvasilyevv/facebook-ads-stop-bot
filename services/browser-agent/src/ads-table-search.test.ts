import test from 'node:test';
import assert from 'node:assert/strict';

import { findToggleCellWithTableScan } from './ads-table.js';

// Проверяем, что поиск toggle сначала использует текущий viewport и не делает лишний reset, если строка уже видна.
test('findToggleCellWithTableScan prefers currently visible row before reset', async () => {
  const cell = { kind: 'cell' };
  const page = {
    $: async (selector: string) => (selector.includes('table_row:120246283878900334') ? cell : null),
  };

  const found = await findToggleCellWithTableScan(page as never, '120246283878900334', {
    resetToTop: true,
  });

  assert.equal(found, cell as never);
});
