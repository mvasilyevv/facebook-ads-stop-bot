import { describe, it, expect } from 'vitest';
import { buildSpendAlertsChartData } from './SpendAlertsChart.jsx';

// Сценарий: из кумулятивного spend API строятся дельты и итог для графика.
describe('buildSpendAlertsChartData', () => {
  it('computes spend deltas from cumulative timeline', () => {
    const rows = buildSpendAlertsChartData(
      [
        { label: '09:00', timestamp: '2026-01-01T09:00:00Z', spend: 10 },
        { label: '09:30', timestamp: '2026-01-01T09:30:00Z', spend: 25 },
        { label: '10:00', timestamp: '2026-01-01T10:00:00Z', spend: 25 },
      ],
      [],
    );

    expect(rows.map((r) => r.spendDelta)).toEqual([10, 15, 0]);
    expect(rows.map((r) => r.spendCumulative)).toEqual([10, 25, 25]);
    expect(rows.at(-1).spendCumulative).toBe(25);
    expect(rows.reduce((sum, r) => sum + r.spendDelta, 0)).toBe(25);
  });

  it('sorts labels by timestamp not alphabetically', () => {
    const rows = buildSpendAlertsChartData(
      [
        { label: '10:00', timestamp: '2026-01-01T10:00:00Z', spend: 5 },
        { label: '09:00', timestamp: '2026-01-01T09:00:00Z', spend: 2 },
      ],
      [],
    );

    expect(rows.map((r) => r.label)).toEqual(['09:00', '10:00']);
  });
});
