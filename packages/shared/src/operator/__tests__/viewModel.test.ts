import { describe, expect, it } from "vitest";

import type {
  OperatorActionsResponse,
  OperatorAdsResponse,
  OperatorSnapshot,
} from "../contracts";
import { makeOperatorSnapshot } from "../testFixture";
import {
  actionForRealtimeState,
  actionProjectionFromResponse,
  actionsForRealtimeState,
  adsForRealtimeState,
  decimalToNumber,
  severityForDataState,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
  workerStatusLabel,
} from "../viewModel";

const snapshot = {
  meta: { cabinet_timezone_known: true },
  attention: { state: "ready", data: { items: [] } },
  system: { state: "ready", data: { severity: "ok" } },
} as unknown as OperatorSnapshot;

const scope: OperatorAdsResponse["scope"] = {
  account_ids: ["act_123"],
  display_timezone: "Europe/Kaliningrad",
  cabinet_timezone: "Europe/Kaliningrad",
  cabinet_timezone_state: "single",
  missing_timezone_account_ids: [],
  currency: "USD",
  currency_state: "single",
  missing_currency_account_ids: [],
  currency_observed_at: "2026-07-18T10:14:45Z",
};

describe("operator view model", () => {
  it("downgrades cached confirmed actions until realtime reconciliation", () => {
    const action = {
      ...makeOperatorSnapshot().actions.data!.items[0]!,
      state: "confirmed" as const,
    };
    const response = {
      state: "ready",
      as_of: action.updated_at,
      freshness_seconds: 5,
      sources: ["task_queue"],
      issues: [],
      scope,
      items: [
        action,
        { ...action, id: "1843", public_id: "#1843", state: "failed" },
      ],
      next_cursor: null,
    } satisfies OperatorActionsResponse;

    const disconnected = actionsForRealtimeState(response, false);

    expect(disconnected.state).toBe("stale");
    expect(disconnected.issues[0]?.code).toBe("REALTIME_RECONCILING");
    expect(disconnected.items[0]?.state).toBe("unknown");
    expect(disconnected.items[1]?.state).toBe("failed");
    expect(response.items[0]?.state).toBe("confirmed");
    expect(actionsForRealtimeState(response, true)).toBe(response);
  });

  it("preserves generated detail metadata and removes stale confirmation", () => {
    const action = {
      ...makeOperatorSnapshot().actions.data!.items[0]!,
      state: "confirmed" as const,
    };
    const response = {
      state: "ready",
      as_of: action.updated_at,
      freshness_seconds: 5,
      sources: ["task_queue"],
      issues: [],
      scope,
      items: [action],
      next_cursor: null,
    } satisfies OperatorActionsResponse;

    const selected = actionProjectionFromResponse(response, action.id);
    const disconnected = actionForRealtimeState(selected, false);

    expect(selected.state).toBe("ready");
    expect(selected.data?.state).toBe("confirmed");
    expect(disconnected.state).toBe("stale");
    expect(disconnected.data?.state).toBe("unknown");
    expect(disconnected.as_of).toBe(response.as_of);
    expect(disconnected.sources).toEqual(response.sources);
  });

  it("does not expose a confirmed action from a stale server response", () => {
    const action = {
      ...makeOperatorSnapshot().actions.data!.items[0]!,
      state: "confirmed" as const,
    };
    const response = {
      state: "stale",
      as_of: action.updated_at,
      freshness_seconds: 60,
      sources: ["task_queue"],
      issues: [],
      scope,
      items: [action],
      next_cursor: null,
    } satisfies OperatorActionsResponse;

    expect(actionsForRealtimeState(response, true).items[0]?.state).toBe(
      "unknown",
    );
    expect(actionProjectionFromResponse(response, action.id).data?.state).toBe(
      "unknown",
    );
  });

  it("downgrades cached ad rows and cannot confirm an empty catalog during reconnect", () => {
    const response = {
      state: "empty",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["meta"],
      issues: [],
      scope,
      rows: [],
      page: 1,
      page_size: 50,
      total: 0,
      pages: 0,
    } satisfies OperatorAdsResponse;

    const disconnected = adsForRealtimeState(response, false);
    expect(disconnected.state).toBe("stale");
    expect(disconnected.issues[0]?.code).toBe("REALTIME_RECONCILING");
    expect(adsForRealtimeState(response, true)).toBe(response);
  });

  it.each([
    ["partial", "partial"],
    ["stale", "stale"],
    ["unavailable", "unavailable"],
  ] as const)(
    "propagates a %s collection state to otherwise ready ad rows",
    (collectionState, expectedRowState) => {
      const response = {
        state: collectionState,
        as_of: "2026-07-18T10:14:45Z",
        freshness_seconds: 15,
        sources: ["meta"],
        issues: [],
        scope,
        rows: [
          {
            data_state: "ready",
            delivery_status: "ACTIVE",
            metrics: {
              spend: "0",
              impressions: 0,
              clicks: 0,
              registrations: 0,
              ftd: 0,
              confirmed_deposits: 0,
              cpc: null,
              cost_per_registration: null,
            },
          },
        ] as OperatorAdsResponse["rows"],
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
      } satisfies OperatorAdsResponse;

      const effective = adsForRealtimeState(response, true);
      expect(effective.rows[0]?.data_state).toBe(expectedRowState);
      expect(effective).not.toBe(response);
    },
  );

  it("removes cached metrics and delivery from unavailable ad rows", () => {
    const row: OperatorAdsResponse["rows"][number] = {
      id: "row-1",
      fb_ad_id: "120001",
      name: "Cached ad",
      campaign_id: "campaign-1",
      campaign_name: "Campaign",
      adset_id: "adset-1",
      adset_name: "Ad set",
      account_id: "act_1",
      delivery_status: "ACTIVE",
      data_state: "unavailable",
      severity: "unknown",
      as_of: null,
      metrics: {
        spend: "0",
        impressions: 0,
        clicks: 0,
        registrations: 0,
        ftd: 0,
        confirmed_deposits: 0,
        cpc: "0",
        cost_per_registration: "0",
        frequency: "0",
        cost_per_ftd: "0",
      },
      // Строка без подтверждённых данных: правило неизвестно целиком, поэтому
      // offer_code=null означает «не подтверждено», а не «оффер не сматчился».
      rule_context: {
        offer_code: null,
        rule_code: null,
        rule_title: null,
        value: null,
        threshold: null,
        percent_to_stop: null,
        stage: null,
      },
      active_action: null,
    };
    const response = {
      state: "unavailable",
      as_of: null,
      freshness_seconds: null,
      sources: ["meta"],
      issues: [],
      scope,
      rows: [row],
      page: 1,
      page_size: 50,
      total: 1,
      pages: 1,
    } satisfies OperatorAdsResponse;

    const effective = adsForRealtimeState(response, true);

    expect(effective.rows[0]?.delivery_status).toBeNull();
    expect(effective.rows[0]?.metrics).toEqual({
      spend: null,
      impressions: null,
      clicks: null,
      registrations: null,
      ftd: null,
      confirmed_deposits: null,
      cpc: null,
      cost_per_registration: null,
      frequency: null,
      cost_per_ftd: null,
    });
    // Близость к стопу производна от метрик: кэшированный порог на строке без
    // данных выглядел бы как актуальная оценка риска.
    expect(effective.rows[0]?.rule_context).toEqual({
      offer_code: null,
      rule_code: null,
      rule_title: null,
      value: null,
      threshold: null,
      percent_to_stop: null,
      stage: null,
    });
    expect(row.metrics.registrations).toBe(0);
  });

  it("keeps unknown money distinct from confirmed zero", () => {
    expect(decimalToNumber(null)).toBeNull();
    expect(decimalToNumber("0")).toBe(0);
  });

  it("translates known worker states without hiding an unknown diagnostic state", () => {
    expect(workerStatusLabel("ONLINE")).toBe("В работе");
    expect(workerStatusLabel("degraded")).toBe("С ограничениями");
    expect(workerStatusLabel("warming_up")).toBe("warming_up");
  });

  it("reports healthy only for a confirmed empty attention section", () => {
    expect(snapshotHeadline(snapshot).severity).toBe("ok");
    expect(
      snapshotHeadline({
        ...snapshot,
        attention: { ...snapshot.attention, state: "unavailable", data: null },
      }).severity,
    ).toBe("unknown");
  });

  it("never reports healthy when cabinet-day timezone is unknown", () => {
    const headline = snapshotHeadline({
      ...snapshot,
      meta: { ...snapshot.meta, cabinet_timezone_known: false },
    });
    expect(headline.severity).toBe("warning");
    expect(headline.title).toBe("Границы суток требуют проверки");
  });

  it("never promotes stale or unavailable sections to a healthy headline", () => {
    expect(
      snapshotHeadline({
        ...snapshot,
        system: { ...snapshot.system, state: "stale" },
      }).severity,
    ).toBe("unknown");
    expect(
      snapshotHeadline({
        ...snapshot,
        system: {
          ...snapshot.system,
          state: "stale",
          data: { ...snapshot.system.data!, severity: "critical" },
        },
      }).severity,
    ).toBe("unknown");
  });

  it("renders partial evidence as degraded, never confirmed healthy", () => {
    expect(
      snapshotHeadline({
        ...snapshot,
        system: { ...snapshot.system, state: "partial" },
      }).severity,
    ).toBe("warning");
  });

  it.each([
    ["economy partial", { section: "economy", state: "partial" }, "warning"],
    ["funnel stale", { section: "funnel", state: "stale" }, "unknown"],
  ] as const)("never reports healthy for %s", (_label, degraded, expected) => {
    const value = makeOperatorSnapshot();
    value.system.data!.severity = "ok";
    value.attention.data!.items = [];
    if (degraded.section === "economy") {
      value.economy = { ...value.economy, state: degraded.state };
    } else {
      value.funnel = { ...value.funnel, state: degraded.state };
    }

    expect(snapshotHeadline(value).severity).toBe(expected);
    expect(snapshotOverviewState(value)).toBe(degraded.state);
  });

  it.each([
    ["currency", { currency: null, currency_state: "mixed" as const }],
    [
      "timezone",
      {
        cabinet_timezone: null,
        cabinet_timezone_known: true,
        cabinet_timezone_state: "mixed" as const,
      },
    ],
  ])("never reports healthy for mixed %s context", (_label, meta) => {
    const value = makeOperatorSnapshot();
    value.system.data!.severity = "ok";
    value.attention.data!.items = [];
    value.meta = { ...value.meta, ...meta };

    expect(snapshotHeadline(value).severity).toBe("warning");
    expect(snapshotOverviewState(value)).toBe("partial");
  });

  it.each([
    [1, "1 сигнал в работе."],
    [2, "2 сигнала в работе."],
    [5, "5 сигналов в работе."],
    [11, "11 сигналов в работе."],
    [21, "21 сигнал в работе."],
  ])(
    "uses the correct Russian form for %i attention items",
    (count, detail) => {
      const result = snapshotHeadline({
        ...snapshot,
        attention: {
          ...snapshot.attention,
          data: { items: Array.from({ length: count }, () => ({}) as never) },
        },
        system: {
          ...snapshot.system,
          data: { ...snapshot.system.data!, severity: "warning" },
        },
      });

      expect(result.detail).toBe(detail);
    },
  );

  it("keeps confirmed critical evidence above the generic partial headline", () => {
    const partialSystem = {
      ...snapshot.system,
      state: "partial" as const,
      data: { ...snapshot.system.data!, severity: "critical" as const },
    };
    const result = snapshotHeadline({ ...snapshot, system: partialSystem });

    expect(result.severity).toBe("critical");
    expect(result.title).toBe("Контур требует немедленного внимания");
    expect(partialSystem.state).toBe("partial");
  });

  it("neutralizes cached worker severity when its section is stale", () => {
    expect(severityForDataState("ok", "stale")).toBe("unknown");
    expect(severityForDataState("critical", "unavailable")).toBe("unknown");
    expect(severityForDataState("ok", "partial")).toBe("warning");
    expect(severityForDataState("critical", "partial")).toBe("critical");
  });

  it("downgrades every usable snapshot section until realtime reconciliation", () => {
    const completeSnapshot = makeOperatorSnapshot();
    completeSnapshot.actions.data!.items[0]!.state = "confirmed";
    const disconnected = snapshotForRealtimeState(completeSnapshot, false);

    expect(disconnected.system.state).toBe("stale");
    expect(disconnected.attention.state).toBe("stale");
    expect(disconnected.actions.data?.items[0]?.state).toBe("unknown");
    expect(disconnected.system.issues[0]?.code).toBe("REALTIME_RECONCILING");
    expect(snapshotHeadline(disconnected).severity).toBe("unknown");
    expect(completeSnapshot.system.state).toBe("ready");
    expect(completeSnapshot.actions.data?.items[0]?.state).toBe("confirmed");
  });

  it("preserves unavailable evidence and returns connected snapshots unchanged", () => {
    const completeSnapshot = makeOperatorSnapshot();
    const unavailable = {
      ...completeSnapshot,
      system: {
        ...completeSnapshot.system,
        state: "unavailable" as const,
        data: null,
      },
    };

    expect(snapshotForRealtimeState(unavailable, false).system.state).toBe(
      "unavailable",
    );
    expect(snapshotForRealtimeState(completeSnapshot, true)).toBe(
      completeSnapshot,
    );
  });

  it("drops cached stop proximity from an unavailable approaching row", () => {
    const completeSnapshot = makeOperatorSnapshot();
    completeSnapshot.approaching_stop = {
      ...completeSnapshot.approaching_stop,
      state: "unavailable",
      data: {
        items: [
          {
            id: "row-1",
            fb_ad_id: "ad-1",
            name: "Cached ad",
            campaign_id: "campaign-1",
            campaign_name: "Campaign",
            adset_id: "adset-1",
            adset_name: "Ad set",
            account_id: "act_123",
            delivery_status: "ACTIVE",
            data_state: "ready",
            severity: "warning",
            as_of: "2026-07-18T10:14:45Z",
            metrics: {
              spend: "12.50",
              impressions: 100,
              clicks: 10,
              registrations: 2,
              ftd: 0,
              confirmed_deposits: 0,
              cpc: "1.25",
              cost_per_registration: "6.25",
              frequency: "1.80",
              cost_per_ftd: null,
            },
            rule_context: {
              offer_code: "GH_CR2",
              rule_code: "cpr_stop",
              rule_title: "Дорогая рега",
              value: "6.25",
              threshold: "7.00",
              percent_to_stop: "89.28",
              stage: "warning",
            },
            active_action: null,
          },
        ],
      },
    };

    const projected = snapshotForRealtimeState(completeSnapshot, true);
    const row = projected.approaching_stop.data?.items[0];

    expect(row?.data_state).toBe("unavailable");
    expect(row?.rule_context.percent_to_stop).toBeNull();
    expect(row?.rule_context.stage).toBeNull();
    expect(row?.metrics.frequency).toBeNull();
  });

  it("marks the approaching stop section stale until realtime reconciliation", () => {
    const disconnected = snapshotForRealtimeState(
      makeOperatorSnapshot(),
      false,
    );

    expect(disconnected.approaching_stop.state).toBe("stale");
    expect(disconnected.approaching_stop.issues[0]?.code).toBe(
      "REALTIME_RECONCILING",
    );
  });
});
