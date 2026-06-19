/**
 * Тесты секции КРЕАТИВ в AdDrawer:
 *   - показывает img превью при наличии creative_thumb_url / creative_image_url;
 *   - скрывает секцию когда оба поля пустые;
 *   - показывает дневной бюджет, пиксель, фазу обучения;
 *   - скрывает строки бюджета/пикселя/learning при отсутствии значений.
 *
 * Используем прямой рендер AdDrawer без роутера — передаём ad как проп.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AdSnapshot } from "@fb/shared";

// ─── Моки ─────────────────────────────────────────────────────────────────────

vi.mock("@/lib/api/ads", () => ({
  useAds: vi.fn(),
  useAdTimeline: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useSnoozeAd: vi.fn(() => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false })),
  useBulkDisable: vi.fn(() => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false })),
}));

vi.mock("@/lib/websocket/useRealtimeInvalidation", () => ({
  useRealtimeInvalidation: vi.fn(() => ({ status: "connected" })),
}));

import { AdDrawer } from "@/components/domain/ads/AdDrawer";

// ─── Фабрика snapshot ────────────────────────────────────────────────────────

type ExtendedAdSnapshot = AdSnapshot & {
  creative_thumb_url?: string | null;
  creative_image_url?: string | null;
  adset_pixel_id?: string | null;
  adset_daily_budget?: string | null;
  adset_lifetime_budget?: string | null;
  adset_budget_remaining?: string | null;
  learning_stage?: string | null;
};

function makeAd(overrides: Partial<ExtendedAdSnapshot> = {}): AdSnapshot {
  return {
    fb_ad_id: "ad-test-001",
    internal_id: "u-test-001",
    ad_name: "GH_CR | static | MV | 19.06",
    campaign_name: "GH_CR | 19.06",
    adset_name: "adset-android",
    offer_code: "GH_CR",
    alert_state: "normal",
    is_active: true,
    last_seen_at: new Date().toISOString(),
    stop_rule_codes: [],
    warning_rule_codes: [],
    metrics: {
      cycle_ts: new Date().toISOString(),
      spend: "100.00",
      leads: 3,
    },
    ...overrides,
  } as AdSnapshot;
}

function wrap(node: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

// ─── Тесты ───────────────────────────────────────────────────────────────────

describe("AdDrawer — секция КРЕАТИВ", () => {
  // Показывает img когда задан creative_thumb_url.
  it("показывает img превью при наличии creative_thumb_url", () => {
    const ad = makeAd({ creative_thumb_url: "https://cdn.example.com/thumb.jpg" });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.getByText("КРЕАТИВ")).toBeInTheDocument();
    const img = screen.getByAltText("Превью крео");
    expect(img).toHaveAttribute("src", "https://cdn.example.com/thumb.jpg");
  });

  // Предпочитает creative_image_url для полного изображения (не thumb).
  it("использует creative_image_url как src если задан (предпочтительней thumb)", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      creative_image_url: "https://cdn.example.com/full.jpg",
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    const img = screen.getByAltText("Превью крео");
    // img.src = full (creative_image_url), ссылка открытия = creative_image_url.
    expect(img).toHaveAttribute("src", "https://cdn.example.com/full.jpg");
  });

  // Скрывает секцию когда оба поля пустые.
  it("не показывает секцию КРЕАТИВ когда creative_thumb_url и creative_image_url равны null", () => {
    const ad = makeAd({ creative_thumb_url: null, creative_image_url: null });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.queryByText("КРЕАТИВ")).not.toBeInTheDocument();
    expect(screen.queryByAltText("Превью крео")).not.toBeInTheDocument();
  });

  // Секция отсутствует когда поля не переданы вовсе (undefined).
  it("не показывает секцию КРЕАТИВ когда поля крео отсутствуют (undefined)", () => {
    const ad = makeAd(); // без creative_* полей
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.queryByText("КРЕАТИВ")).not.toBeInTheDocument();
  });

  // Показывает дневной бюджет в читаемом виде (minor units / 100).
  it("показывает дневной бюджет (Бюджет (день)) из adset_daily_budget", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      adset_daily_budget: "150000", // 1500.00
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.getByText("Бюджет (день)")).toBeInTheDocument();
    // 150000 / 100 = 1500.00 — локаль ru-RU: «1 500,00»
    expect(screen.getByText(/1\s*500/)).toBeInTheDocument();
  });

  // Показывает пиксель моноширинно.
  it("показывает adset_pixel_id", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      adset_pixel_id: "987654321",
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.getByText("Пиксель")).toBeInTheDocument();
    expect(screen.getByText("987654321")).toBeInTheDocument();
  });

  // Показывает бейдж «Обучение» для LEARNING (строка «Обучение» — лейбл строки + бейдж).
  it("показывает бейдж «Обучение» для learning_stage=LEARNING", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      learning_stage: "LEARNING",
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    // «Обучение» встречается дважды: лейбл строки и бейдж — оба в DOM.
    expect(screen.getAllByText("Обучение").length).toBeGreaterThanOrEqual(1);
  });

  // Показывает бейдж «Обучение ограничено» для LEARNING_LIMITED.
  it("показывает бейдж «Обучение ограничено» для learning_stage=LEARNING_LIMITED", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      learning_stage: "LEARNING_LIMITED",
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.getByText("Обучение ограничено")).toBeInTheDocument();
  });

  // Пустой learning_stage (строка "") — строку «Обучение» не показываем.
  it("не показывает строку Обучение при пустом learning_stage", () => {
    const ad = makeAd({
      creative_thumb_url: "https://cdn.example.com/thumb.jpg",
      learning_stage: "",
    });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    // Секция КРЕАТИВ есть (thumb задан), но «Обучение» — нет.
    expect(screen.getByText("КРЕАТИВ")).toBeInTheDocument();
    expect(screen.queryByText("Обучение")).not.toBeInTheDocument();
  });

  // Скрывает строки бюджета/пикселя/learning при отсутствии всех дополнительных полей.
  it("не показывает блок бюджет/пиксель/learning когда все поля отсутствуют", () => {
    const ad = makeAd({ creative_thumb_url: "https://cdn.example.com/thumb.jpg" });
    wrap(<AdDrawer ad={ad} onClose={vi.fn()} />);
    expect(screen.queryByText("Бюджет (день)")).not.toBeInTheDocument();
    expect(screen.queryByText("Пиксель")).not.toBeInTheDocument();
    expect(screen.queryByText("Обучение")).not.toBeInTheDocument();
  });
});
