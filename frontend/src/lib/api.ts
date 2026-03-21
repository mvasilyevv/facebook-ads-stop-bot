import type {
  AdSummary,
  ApiErrorResponse,
  BotModeResponse,
  BrowserSessionItem,
  DecisionItem,
  HealthResponse,
  OfferBindingItem,
  OfferItem,
  OfferRateItem,
  RuleItem,
  ScanRunItem,
} from "../types";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type DashboardPayload = {
  health: HealthResponse | null;
  ads: AdSummary[];
  decisions: DecisionItem[];
  rules: RuleItem[];
  offers: OfferItem[];
  bindings: OfferBindingItem[];
  sessions: BrowserSessionItem[];
  errors: Record<string, string>;
};

function buildUrl(path: string): string {
  if (apiBaseUrl) {
    return `${apiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  }
  return path;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let message = "Не удалось получить данные с сервера";
    try {
      const payload = (await response.json()) as ApiErrorResponse;
      message = payload.detail || payload.message || message;
    } catch {
      message = `Сервер вернул ошибку ${response.status}`;
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/health");
}

export function fetchAds(): Promise<AdSummary[]> {
  return requestJson<AdSummary[]>("/ads");
}

export function fetchDecisions(): Promise<DecisionItem[]> {
  return requestJson<DecisionItem[]>("/decisions");
}

export function fetchRules(): Promise<RuleItem[]> {
  return requestJson<RuleItem[]>("/rules");
}

export function fetchOffers(): Promise<OfferItem[]> {
  return requestJson<OfferItem[]>("/offers");
}

export function fetchSessions(): Promise<BrowserSessionItem[]> {
  return requestJson<BrowserSessionItem[]>("/sessions");
}

export function fetchOfferBindings(): Promise<OfferBindingItem[]> {
  return requestJson<OfferBindingItem[]>("/offer-bindings");
}

export function fetchScanRuns(): Promise<ScanRunItem[]> {
  return requestJson<ScanRunItem[]>("/scan-runs");
}

export async function loadDashboard(): Promise<DashboardPayload> {
  const keys = [
    ["health", fetchHealth()],
    ["ads", fetchAds()],
    ["decisions", fetchDecisions()],
    ["rules", fetchRules()],
    ["offers", fetchOffers()],
    ["bindings", fetchOfferBindings()],
    ["sessions", fetchSessions()],
  ] as const;

  const settled = await Promise.allSettled(keys.map(([, promise]) => promise));

  const payload: DashboardPayload = {
    health: null,
    ads: [],
    decisions: [],
    rules: [],
    offers: [],
    bindings: [],
    sessions: [],
    errors: {},
  };

  settled.forEach((result, index) => {
    const key = keys[index][0];
    if (result.status === "fulfilled") {
      switch (key) {
        case "health":
          payload.health = result.value as HealthResponse;
          break;
        case "ads":
          payload.ads = result.value as AdSummary[];
          break;
        case "decisions":
          payload.decisions = result.value as DecisionItem[];
          break;
        case "rules":
          payload.rules = result.value as RuleItem[];
          break;
        case "offers":
          payload.offers = result.value as OfferItem[];
          break;
        case "bindings":
          payload.bindings = result.value as OfferBindingItem[];
          break;
        case "sessions":
          payload.sessions = result.value as BrowserSessionItem[];
          break;
      }
    } else {
      payload.errors[key] =
        result.reason instanceof Error ? result.reason.message : "Неизвестная ошибка";
    }
  });

  return payload;
}

export async function blockAd(fbAdId: string, reason: string): Promise<AdSummary> {
  return requestJson<AdSummary>(`/ads/${fbAdId}/block`, {
    method: "POST",
    body: JSON.stringify({ reason, created_by: "оператор UI" }),
  });
}

export async function unblockAd(fbAdId: string): Promise<AdSummary> {
  return requestJson<AdSummary>(`/ads/${fbAdId}/unblock`, {
    method: "POST",
  });
}

export async function saveRule(ruleId: string, payload: Partial<RuleItem>): Promise<RuleItem> {
  return requestJson<RuleItem>(`/rules/${ruleId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function createOffer(payload: {
  code: string;
  name: string;
  is_active: boolean;
}): Promise<OfferItem> {
  const response = await requestJson<{ offer: OfferItem }>("/offers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return response.offer;
}

export async function createOfferRate(
  offerId: string,
  payload: { cpa_usd: string; effective_from: string; effective_to?: string; note?: string },
): Promise<OfferRateItem> {
  const response = await requestJson<{ rate: OfferRateItem }>(`/offers/${offerId}/rates`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return response.rate;
}

export async function createOfferBinding(payload: {
  path: "ad" | "adset";
  entityId: string;
  offerId: string;
  priority: number;
  isActive: boolean;
}): Promise<OfferBindingItem> {
  const endpoint =
    payload.path === "ad"
      ? `/ads/${payload.entityId}/offer-binding`
      : `/adsets/${payload.entityId}/offer-binding`;
  const response = await requestJson<{ binding: OfferBindingItem }>(endpoint, {
    method: "POST",
    body: JSON.stringify({
      offer_id: payload.offerId,
      priority: payload.priority,
      is_active: payload.isActive,
    }),
  });
  return response.binding;
}

export async function startSession(payload: {
  profileId: string;
  browserHostId: string;
  reason: string;
}): Promise<BrowserSessionItem> {
  const response = await requestJson<{ session: BrowserSessionItem }>(`/sessions/${payload.profileId}/start`, {
    method: "POST",
    body: JSON.stringify({
      browser_host_id: payload.browserHostId,
      reason: payload.reason,
    }),
  });
  return response.session;
}

export async function stopSession(payload: {
  profileId: string;
  browserHostId: string;
  reason: string;
}): Promise<BrowserSessionItem> {
  const response = await requestJson<{ session: BrowserSessionItem }>(`/sessions/${payload.profileId}/stop`, {
    method: "POST",
    body: JSON.stringify({
      browser_host_id: payload.browserHostId,
      reason: payload.reason,
    }),
  });
  return response.session;
}

export function fetchBotMode(): Promise<BotModeResponse> {
  return requestJson<BotModeResponse>("/settings/bot-mode");
}

export async function updateBotMode(payload: {
  auto_pause_enabled: boolean;
  auto_resume_enabled: boolean;
}): Promise<BotModeResponse> {
  return requestJson<BotModeResponse>("/settings/bot-mode", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
