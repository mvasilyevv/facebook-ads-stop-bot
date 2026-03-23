import { http, HttpResponse } from "msw";
import type {
  AdSummary,
  BrowserSessionItem,
  DecisionItem,
  HealthResponse,
  RuleItem,
  ScanRunItem,
  ServiceSettingsResponse,
  OfferItem,
} from "../../src/types";

type DashboardHandlersInput = {
  health: HealthResponse;
  ads: AdSummary[];
  decisions: DecisionItem[];
  rules: RuleItem[];
  offers: OfferItem[];
  sessions: BrowserSessionItem[];
  serviceSettings: ServiceSettingsResponse;
  scanRuns?: ScanRunItem[];
};

export function buildDashboardHandlers(input: DashboardHandlersInput) {
  return [
    http.get("*/health", () => HttpResponse.json(input.health)),
    http.get("*/ads", () => HttpResponse.json(input.ads)),
    http.get("*/decisions", () => HttpResponse.json(input.decisions)),
    http.get("*/rules", () => HttpResponse.json(input.rules)),
    http.get("*/offers", () => HttpResponse.json(input.offers)),
    http.get("*/sessions", () => HttpResponse.json(input.sessions)),
    http.get("*/settings/service", () => HttpResponse.json(input.serviceSettings)),
    http.get("*/scan-runs", () => HttpResponse.json(input.scanRuns ?? [])),
  ];
}
