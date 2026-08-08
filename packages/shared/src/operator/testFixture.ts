import type { OperatorScopeEvidence, OperatorSnapshot } from "./contracts";

/** Deterministic account-context evidence shared by operator response fixtures. */
export function makeOperatorScopeEvidence(): OperatorScopeEvidence {
  return {
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
}

/** Deterministic contract fixture for frontend, TMA and contract tests. */
export function makeOperatorSnapshot(): OperatorSnapshot {
  return {
    meta: {
      revision: "r2a",
      sequence: 42,
      generated_at: "2026-07-18T10:15:00Z",
      timezone: "Europe/Kaliningrad",
      cabinet_timezone: "Europe/Kaliningrad",
      cabinet_timezone_known: true,
      cabinet_timezone_state: "single",
      missing_timezone_account_ids: [],
      currency: "USD",
      currency_state: "single",
      missing_currency_account_ids: [],
      currency_observed_at: "2026-07-18T10:14:45Z",
      window: "today",
      account: { id: "act_123", name: "Main cabinet" },
      cabinet_day: {
        starts_at: "2026-07-18T00:00:00+02:00",
        ends_at: "2026-07-19T00:00:00+02:00",
      },
    },
    attention: {
      state: "ready",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["observer", "tracker"],
      issues: [],
      data: {
        items: [
          {
            id: "incident-1",
            kind: "incident",
            severity: "warning",
            title: "CPL выше базы",
            summary: "CPL USD 9.56 при базе USD 3.00.",
            reason: "Расход растёт без FTD",
            occurred_at: "2026-07-18T10:10:00Z",
            target: { kind: "ad", id: "ad-1", label: "GH_CR2" },
            action: { label: "Открыть объявление", href: "/ads/ad-1" },
          },
        ],
      },
    },
    economy: {
      state: "ready",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["meta"],
      issues: [],
      data: {
        totals: {
          spend: "18.40",
          base: "15.00",
          stop: "30.00",
          base_delta: "3.40",
        },
        series: [
          {
            at: "2026-07-18T08:00:00Z",
            actual: "4.20",
            base: "5.00",
            stop: "10.00",
          },
          {
            at: "2026-07-18T09:00:00Z",
            actual: null,
            base: "10.00",
            stop: "20.00",
          },
          {
            at: "2026-07-18T10:00:00Z",
            actual: "18.40",
            base: "15.00",
            stop: "30.00",
          },
        ],
      },
    },
    funnel: {
      state: "ready",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["meta", "tracker"],
      issues: [],
      data: {
        stages: [
          {
            key: "clicks",
            label: "Клики",
            count: 42,
            conversion: "100",
            cost: "0.44",
          },
          {
            key: "registrations",
            label: "Регистрации",
            count: 5,
            conversion: "11.90",
            cost: "3.68",
          },
          {
            key: "ftd",
            label: "FTD",
            count: 1,
            conversion: "20.00",
            cost: "18.40",
          },
          {
            key: "confirmed_deposits",
            label: "Подтверждённые депозиты",
            count: 1,
            conversion: "100",
            cost: "18.40",
          },
        ],
      },
    },
    actions: {
      state: "ready",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["task_queue"],
      issues: [],
      data: {
        items: [
          {
            id: "1842",
            public_id: "#1842",
            kind: "pause",
            state: "running",
            title: "Отключение объявления",
            target_label: "GH_CR2",
            requested_at: "2026-07-18T10:12:00Z",
            updated_at: "2026-07-18T10:13:00Z",
            requested_by: "operator",
            reason: null,
            correlation_id: "corr-1",
            account_id: "act_123",
            currency: "USD",
            cabinet_timezone: "Europe/Kaliningrad",
            account_context_observed_at: "2026-07-18T10:14:45Z",
            account_context_issues: [],
          },
        ],
      },
    },
    system: {
      state: "ready",
      as_of: "2026-07-18T10:14:45Z",
      freshness_seconds: 15,
      sources: ["postgresql", "cabinet_runtime"],
      issues: [],
      data: {
        severity: "warning",
        monitoring_enabled: true,
        last_scan_at: "2026-07-18T10:14:30Z",
        next_scan_at: "2026-07-18T10:15:00Z",
        workers: [
          {
            id: "observer",
            label: "Observer",
            severity: "ok",
            status: "online",
            last_activity_at: "2026-07-18T10:14:45Z",
          },
          {
            id: "browser",
            label: "Browser agent",
            severity: "warning",
            status: "degraded",
            last_activity_at: "2026-07-18T10:14:40Z",
          },
        ],
      },
    },
  };
}
