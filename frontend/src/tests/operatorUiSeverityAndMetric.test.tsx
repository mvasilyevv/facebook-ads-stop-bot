import { render, screen } from "@testing-library/react";

import {
  deliveryStatusTextClass,
  incidentSeverityTone,
  Metric,
  MetricCell,
  severityToneClass,
} from "@fb/operator-ui";

describe("severityToneClass / deliveryStatusTextClass", () => {
  it("критичное — полужирный danger", () => {
    expect(severityToneClass("critical")).toContain("text-danger");
    expect(severityToneClass("critical")).toContain("font-semibold");
  });

  it("предупреждение — полужирный warning", () => {
    expect(severityToneClass("warning")).toContain("text-warning");
    expect(severityToneClass("warning")).toContain("font-semibold");
  });

  it("ok и unknown — нейтральный тон, не зелёный и не жирный", () => {
    expect(severityToneClass("ok")).not.toContain("text-success");
    expect(severityToneClass("ok")).not.toContain("font-semibold");
    expect(severityToneClass("unknown")).not.toContain("font-semibold");
  });

  it("deliveryStatusTextClass делегирует severity статуса доставки", () => {
    expect(deliveryStatusTextClass("DISAPPROVED")).toContain("text-danger");
    expect(deliveryStatusTextClass(null)).toBe(severityToneClass("unknown"));
  });
});

describe("incidentSeverityTone", () => {
  it("честное состояние окрашивает по severity", () => {
    expect(incidentSeverityTone("critical", "ready").surface).toContain("bg-danger-bg");
    expect(incidentSeverityTone("warning", "ready").surface).toContain("bg-warning-bg");
    expect(incidentSeverityTone("ok", "ready").surface).toContain("bg-success-bg");
  });

  // Инвариант проекта: partial/stale/unavailable никогда не выглядят
  // зелёными и не читаются критичнее реальной severity — любое нечестное
  // состояние данных получает warning-поверхность, а не danger/success.
  it.each(["empty", "partial", "stale", "unavailable"] as const)(
    "state=%s всегда даёт warning-поверхность независимо от severity",
    (state) => {
      for (const severity of ["ok", "warning", "critical", "unknown"] as const) {
        const tone = incidentSeverityTone(severity, state);
        expect(tone.surface).toContain("bg-warning-bg");
        expect(tone.surface).not.toContain("bg-danger-bg");
        expect(tone.surface).not.toContain("bg-success-bg");
      }
    },
  );
});

describe("MetricCell / Metric", () => {
  it("MetricCell рендерит td со значением в font-numeric", () => {
    render(
      <table>
        <tbody>
          <tr>
            <MetricCell value="1 234" />
          </tr>
        </tbody>
      </table>,
    );
    const cell = screen.getByText("1 234");
    expect(cell.tagName).toBe("TD");
    expect(cell.className).toContain("font-numeric");
    expect(cell.className).toContain("tabular-nums");
  });

  it("Metric рендерит dt/dd, значение в font-numeric", () => {
    render(
      <dl>
        <Metric label="Расход" value="$12.50" />
      </dl>,
    );
    expect(screen.getByText("Расход").tagName).toBe("DT");
    const value = screen.getByText("$12.50");
    expect(value.tagName).toBe("DD");
    expect(value.className).toContain("font-numeric");
    expect(value.className).toContain("tabular-nums");
  });
});
