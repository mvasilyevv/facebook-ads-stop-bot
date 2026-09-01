import { describe, expect, it } from "vitest";

import type { OperatorIncidentItem } from "../contracts";
import {
  operatorIncidentCountLabel,
  operatorIncidentCopy,
  operatorIncidentDataState,
  operatorIncidentsQuery,
  operatorIncidentTargetLabel,
  parseOperatorIncidentsRouteSearch,
} from "../incidentViewModel";
import { makeOperatorScopeEvidence } from "../testFixture";

const INCIDENT: OperatorIncidentItem = {
  id: "00000000-0000-0000-0000-000000000051",
  severity: "critical",
  status: "open",
  title: "CPL $9.56 > $3.00",
  summary: "Spend $18.40 · 0 FTD",
  reason: "расход без первого депозита",
  occurred_at: "2026-08-08T12:00:00Z",
  account_id: "123",
  target: { kind: "ad", id: "120001", label: "GH_CR2" },
  action: {
    label: "Открыть",
    href: "/incidents/00000000-0000-0000-0000-000000000051",
  },
  requires_usd_evidence: true,
};

describe("operator incident view-model", () => {
  it("keeps filters in bounded URL state; paging is cursor-owned, not URL-owned", () => {
    const parsed = parseOperatorIncidentsRouteSearch({
      account_id: " 123 ",
      severity: "critical",
      status: "acknowledged",
    });

    expect(parsed).toEqual({
      account_id: "123",
      severity: "critical",
      status: "acknowledged",
    });
    expect(operatorIncidentsQuery(parsed, 30)).toEqual({
      account_id: "123",
      severity: ["critical"],
      status: ["acknowledged"],
      page_size: 30,
    });
    expect(
      parseOperatorIncidentsRouteSearch({
        severity: "danger",
        status: "done",
      }),
    ).toEqual({
      account_id: undefined,
      severity: undefined,
      status: undefined,
    });
  });

  it("suppresses the sum but never the title when currency isn't a confirmed single value", () => {
    const unknownScope = {
      ...makeOperatorScopeEvidence(),
      currency: null,
      currency_state: "unknown" as const,
      currency_observed_at: null,
      missing_currency_account_ids: ["123"],
    };
    const mixedScope = {
      ...makeOperatorScopeEvidence(),
      currency: null,
      currency_state: "mixed" as const,
    };

    // Issue 354: the title names which rule fired, not the sum — it is never
    // replaced, and unknown vs mixed read as two different reasons.
    expect(operatorIncidentCopy(INCIDENT, unknownScope)).toEqual({
      title: INCIDENT.title,
      summary:
        "Валюта кабинета не подтверждена. Обновите снимок — денежные детали скрыты.",
      reason: null,
    });
    expect(operatorIncidentCopy(INCIDENT, mixedScope)).toEqual({
      title: INCIDENT.title,
      summary:
        "В выборке несколько валют. Сузьте до одного кабинета — денежные детали скрыты.",
      reason: null,
    });
    // A single confirmed non-USD currency hides nothing — it is already
    // denominated correctly server-side.
    expect(
      operatorIncidentCopy(INCIDENT, {
        ...makeOperatorScopeEvidence(),
        currency: "EUR",
        currency_state: "single" as const,
      }),
    ).toEqual({
      title: INCIDENT.title,
      summary: INCIDENT.summary,
      reason: INCIDENT.reason,
    });
    expect(operatorIncidentCopy(INCIDENT, makeOperatorScopeEvidence())).toEqual(
      {
        title: INCIDENT.title,
        summary: INCIDENT.summary,
        reason: INCIDENT.reason,
      },
    );
  });

  it("never turns an opaque target id into visible fallback copy", () => {
    expect(
      operatorIncidentTargetLabel({
        ...INCIDENT,
        target: {
          kind: "system",
          id: "00000000-0000-0000-0000-000000000099",
          label: null,
        },
      }),
    ).toBe("Объект не указан");
  });

  it("never labels disconnected incident evidence as ready or confirmed empty", () => {
    expect(operatorIncidentDataState("ready", false)).toBe("stale");
    expect(operatorIncidentDataState("empty", false)).toBe("stale");
    expect(operatorIncidentDataState("partial", false)).toBe("stale");
    expect(operatorIncidentDataState("unavailable", false)).toBe("unavailable");
    expect(operatorIncidentDataState("ready", true)).toBe("ready");
  });

  it("formats Russian incident counts without broken grammar", () => {
    expect([1, 2, 5, 11, 22].map(operatorIncidentCountLabel)).toEqual([
      "1 запись",
      "2 записи",
      "5 записей",
      "11 записей",
      "22 записи",
    ]);
  });
});
