import type { Page } from "@playwright/test";

import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "../../packages/shared/src/operator/testFixture";
import { makeAnalyticsPerformanceFixture } from "../src/tests/analytics/analyticsFixture";

const operatorScope = makeOperatorScopeEvidence();

export const operatorAd = {
  id: "row-1",
  fb_ad_id: "ad-1",
  name: "GH_CR2 · Основное объявление",
  campaign_id: "campaign-1",
  campaign_name: "GH · CR2",
  adset_id: "adset-1",
  adset_name: "Broad",
  account_id: "123",
  delivery_status: "ACTIVE",
  data_state: "ready",
  severity: "warning",
  as_of: "2026-07-18T10:15:00Z",
  metrics: {
    spend: "18.40",
    impressions: 1200,
    clicks: 42,
    registrations: 5,
    ftd: 1,
    confirmed_deposits: 1,
    cpc: "0.4381",
    cost_per_registration: "3.68",
    frequency: "1.84",
    cost_per_ftd: "18.40",
  },
  rule_context: {
    offer_code: "GH_CR2",
    rule_code: "cpr_stop",
    rule_title: "Дорогая рега",
    value: "3.68",
    threshold: "4.00",
    percent_to_stop: "92.00",
    stage: "warning",
  },
  active_action: null,
} as const;

export const campaignRunId = "11111111-2222-3333-4444-555555555555";
export const campaignRunOfferCode = "GH_CR2";

const campaignRunSummary = {
  id: campaignRunId,
  preset_id: null,
  status: "creating",
  offer_code: campaignRunOfferCode,
  idempotency_key: "campaign-run-e2e",
  error: null,
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:01:00Z",
} as const;

const campaignRunDetail = {
  ...campaignRunSummary,
  config: {},
  progress: { stage: "creating", completed: 1, total: 3 },
  created_meta_ids: {},
  task: {
    id: 1842,
    state: "running",
    queue_status: "running",
    outcome: null,
    attempt_count: 1,
    max_attempts: 3,
    external_started: false,
    cancel_requested_at: null,
    deadline_at: "2026-07-29T10:05:00Z",
    correlation_id: "campaign-correlation-e2e",
    result: null,
  },
  controls: {
    abort: { available: true, reason: "abort_available" },
    resume: { available: false, reason: "run_not_terminal" },
  },
} as const;

export interface OperatorHarnessOptions {
  telegram?: boolean;
  reloginRequired?: boolean;
}

/**
 * Installs one deterministic operator boundary for web and TMA acceptance.
 * All reads come from the same revision and the WebSocket immediately asks the
 * client to reconcile it, so money controls only unlock after a real query pass.
 */
export async function installOperatorHarness(
  page: Page,
  { telegram = false, reloginRequired = false }: OperatorHarnessOptions = {},
) {
  if (telegram) {
    await page.route("https://telegram.org/js/**", (route) => route.abort());
    await page.route("**/api/tma/auth", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fulfill({ status: 405 });
        return;
      }
      await route.fulfill({
        json: { token: "e2e-current-launch-token", role: "owner" },
      });
    });
  }
  await page.addInitScript(
    ({ withTelegram }) => {
      class ReconciledOperatorSocket extends EventTarget {
        static readonly CONNECTING = 0;
        static readonly OPEN = 1;
        static readonly CLOSING = 2;
        static readonly CLOSED = 3;
        readonly url: string;
        readonly protocol = "fb-operator-v1";
        readonly extensions = "";
        readonly bufferedAmount = 0;
        readonly binaryType = "blob";
        readyState = ReconciledOperatorSocket.CONNECTING;

        constructor(url: string | URL) {
          super();
          this.url = String(url);
          window.setTimeout(() => {
            this.readyState = ReconciledOperatorSocket.OPEN;
            this.dispatchEvent(new Event("open"));
            this.dispatchEvent(
              new MessageEvent("message", {
                data: JSON.stringify({
                  type: "snapshot_required",
                  sequence: 1,
                  snapshot_revision: "r2a",
                  scopes: ["snapshot"],
                  ts: "2026-07-18T10:15:00Z",
                }),
              }),
            );
          }, 0);
        }

        close() {
          this.readyState = ReconciledOperatorSocket.CLOSED;
          this.dispatchEvent(new Event("close"));
        }

        send() {}
      }

      Object.defineProperty(window, "WebSocket", {
        configurable: true,
        value: ReconciledOperatorSocket,
      });

      if (!withTelegram) return;

      // Seed the retired origin-persisted identity. The current launch must
      // ignore and erase it, then authenticate the initData through the route
      // above before mounting protected UI or opening realtime.
      localStorage.setItem("tma_token", "e2e-retired-owner-token");
      localStorage.setItem("tma_role", "owner");

      const events: Record<string, () => void> = {};
      const harness = {
        events,
        readyCalls: 0,
        expandCalls: 0,
        backShowCalls: 0,
        backHideCalls: 0,
        backHandler: null as (() => void) | null,
      };
      const webApp = {
        initData: "query_id=e2e",
        initDataUnsafe: {
          user: { id: 42, first_name: "Operator" },
        },
        themeParams: {},
        BackButton: {
          isVisible: false,
          show() {
            harness.backShowCalls += 1;
            this.isVisible = true;
          },
          hide() {
            harness.backHideCalls += 1;
            this.isVisible = false;
          },
          onClick(callback: () => void) {
            harness.backHandler = callback;
          },
          offClick(callback: () => void) {
            if (harness.backHandler === callback) harness.backHandler = null;
          },
        },
        HapticFeedback: {
          impactOccurred() {},
          notificationOccurred() {},
          selectionChanged() {},
        },
        MainButton: {
          isVisible: false,
          show() {},
          hide() {},
          setText() {},
          onClick() {},
          offClick() {},
        },
        viewportHeight: 680,
        viewportStableHeight: 700,
        safeAreaInset: { top: 10, right: 11, bottom: 12, left: 13 },
        contentSafeAreaInset: { top: 20, right: 21, bottom: 22, left: 23 },
        isExpanded: false,
        expand() {
          harness.expandCalls += 1;
          this.isExpanded = true;
        },
        close() {},
        ready() {
          harness.readyCalls += 1;
        },
        setHeaderColor() {},
        setBackgroundColor() {},
        setBottomBarColor() {},
        onEvent(event: string, callback: () => void) {
          events[event] = callback;
        },
        offEvent(event: string, callback: () => void) {
          if (events[event] === callback) delete events[event];
        },
        showAlert(_message: string, callback?: () => void) {
          callback?.();
        },
        showConfirm(_message: string, callback: (confirmed: boolean) => void) {
          callback(true);
        },
        openLink() {},
        openTelegramLink() {},
      };

      Object.assign(window, {
        __tmaHarness: harness,
        Telegram: { WebApp: webApp },
      });
    },
    { withTelegram: telegram },
  );

  await page.route("**/api/operator/snapshot**", async (route) => {
    const snapshot = makeOperatorSnapshot();
    if (reloginRequired && snapshot.attention.data?.items[0]) {
      snapshot.attention.data.items[0].recovery_action = "retry_scan";
    }
    await route.fulfill({ json: snapshot });
  });
  await page.route("**/api/operator/preferences/display**", async (route) => {
    await route.fulfill({
      json: {
        timezone_name: "Europe/Kaliningrad",
        updated_at: "2026-07-18T10:15:00Z",
      },
    });
  });
  await page.route("**/api/operator/cabinets/*/snapshot**", async (route) => {
    const cabinetId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "123";
    const snapshot = makeOperatorSnapshot();
    const group = snapshot.portfolio.data?.currency_groups[0];
    const cabinet = group?.cabinets.find((candidate) => candidate.id === cabinetId);
    if (group && cabinet) {
      group.cabinets = [cabinet];
      group.totals = cabinet.totals;
      group.state = cabinet.state;
      group.severity = cabinet.severity;
      snapshot.portfolio.state = cabinet.state;
      snapshot.portfolio.as_of = cabinet.as_of;
      snapshot.portfolio.freshness_seconds = cabinet.freshness_seconds;
      snapshot.meta.account = { id: cabinet.id, name: cabinet.name };
      snapshot.meta.cabinet_timezone = cabinet.timezone;
      snapshot.meta.cabinet_timezone_known = cabinet.timezone !== null;
    }
    await route.fulfill({ json: snapshot });
  });
  await page.route("**/api/operator/scan/retry", async (route) => {
    await route.fulfill({
      status: 202,
      json: {
        task_id: 1843,
        public_id: "#1843",
        state: "queued",
        created: true,
        correlation_id: "corr-1843",
      },
    });
  });
  await page.route("**/api/operator/actions**", async (route) => {
    await route.fulfill({
      json: {
        scope: operatorScope,
        state: "ready",
        as_of: "2026-07-18T10:15:00Z",
        freshness_seconds: 0,
        sources: ["postgresql"],
        issues: [],
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
            requested_by: "operator:web",
            reason: null,
            correlation_id: "corr-1842",
            account_id: "act_123",
            currency: "USD",
            cabinet_timezone: "Europe/Kaliningrad",
            account_context_observed_at: "2026-07-18T10:14:45Z",
            account_context_issues: [],
          },
        ],
        next_cursor: null,
      },
    });
  });
  await page.route("**/api/operator/ads**", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 202,
        json: {
          task_id: 1842,
          public_id: "#1842",
          state: "queued",
          created: true,
          correlation_id: "corr-1842",
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        scope: operatorScope,
        state: "ready",
        as_of: "2026-07-18T10:15:00Z",
        freshness_seconds: 0,
        sources: ["meta", "adsetpro"],
        issues: [],
        rows: [operatorAd],
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
      },
    });
  });
  await page.route("**/api/analytics/performance**", async (route) => {
    await route.fulfill({ json: makeAnalyticsPerformanceFixture() });
  });
  await page.route("**/api/analytics/live-budget**", async (route) => {
    const performance = makeAnalyticsPerformanceFixture();
    await route.fulfill({
      json: {
        state: "partial",
        as_of: "2026-07-21T09:59:40Z",
        freshness_seconds: 20,
        issues: ["Одна точка Meta отсутствует"],
        sources: performance.sources,
        scope: performance.scope,
        window: performance.window,
        points: [
          {
            ts: "2026-07-21T08:00:00Z",
            actual: "4.20",
            base: "5.00",
            stop: "10.00",
            available_ads: 1,
            unavailable_ads: 0,
          },
          {
            ts: "2026-07-21T09:00:00Z",
            actual: null,
            base: "10.00",
            stop: "20.00",
            available_ads: 0,
            unavailable_ads: 1,
          },
          {
            ts: "2026-07-21T10:00:00Z",
            actual: "18.40",
            base: "15.00",
            stop: "30.00",
            available_ads: 1,
            unavailable_ads: 0,
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/pulse", async (route) => {
    await route.fulfill({
      json: { important: false, text: "", generated_at: "2026-07-21T10:00:00Z" },
    });
  });
  await page.route("**/api/tools/campaigns/presets", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/settings/observer", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      json: {
        is_scanning_enabled: true,
        default_interval_seconds: 60,
        owner_campaign_tag: null,
        campaign_ids: [],
        am_columns: [],
        am_columns_use_default: true,
        am_column_options: [],
      },
    });
  });
  await page.route("**/api/tools/campaigns/draft**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { draft: null } });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/tools/campaigns/runs**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "POST" && pathname.endsWith(`/${campaignRunId}/abort`)) {
      await route.fulfill({
        status: 202,
        json: {
          action: "abort",
          run_id: campaignRunId,
          task_id: 1842,
          state: "queued",
          run_status: "creating",
          created: true,
          correlation_id: "campaign-correlation-e2e",
          reason: "cooperative_abort_requested",
        },
      });
      return;
    }
    if (request.method() === "POST" && pathname.endsWith(`/${campaignRunId}/resume`)) {
      await route.fulfill({
        status: 202,
        json: {
          action: "resume",
          run_id: campaignRunId,
          task_id: 1843,
          state: "queued",
          run_status: "queued",
          created: true,
          correlation_id: "campaign-correlation-e2e",
          reason: "resume_queued",
        },
      });
      return;
    }
    if (request.method() === "GET" && pathname.endsWith(`/${campaignRunId}`)) {
      await route.fulfill({ json: campaignRunDetail });
      return;
    }
    if (request.method() === "GET" && pathname.endsWith("/runs")) {
      await route.fulfill({
        headers: { "X-Total-Count": "1" },
        json: [campaignRunSummary],
      });
      return;
    }
    await route.fallback();
  });
}
