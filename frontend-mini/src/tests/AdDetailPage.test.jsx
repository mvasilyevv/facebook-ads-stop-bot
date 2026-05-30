import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// Мокаем api.js — проверяем, что страница рендерит новый shape GET /tma/ads/{id}.
vi.mock("../api.js", () => ({
  getAdDetail: vi.fn(),
  disableAd: vi.fn(),
  snoozeAd: vi.fn(),
  claimAd: vi.fn(),
}));

import { getAdDetail } from "../api.js";
import AdDetailPage from "../pages/AdDetailPage.jsx";

function renderAt(fbAdId) {
  return render(
    <MemoryRouter initialEntries={[`/ads/${fbAdId}`]}>
      <Routes>
        <Route path="/ads/:fbAdId" element={<AdDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AdDetailPage", () => {
  // Активный инцидент: state-бейдж, метрики и кнопка «Снять алерт» видны
  it("рендерит STOP_SENT, метрики и кнопку Снять алерт", async () => {
    getAdDetail.mockResolvedValue({
      fb_ad_id: "AD1",
      ad_name: "Тест-объявление",
      campaign_name: "C",
      adset_name: "A",
      state: "STOP_SENT",
      snooze_until: null,
      metrics: { spend: "12.50", leads: 4, deposits: 0, cpc: "0.12", ctr: "1.5" },
      recent_alerts: [],
      can_open_in_ads_manager: false,
    });
    renderAt("AD1");
    await waitFor(() => screen.getByText("Тест-объявление"));
    expect(screen.getByText("Стоп")).toBeInTheDocument(); // STATE_LABELS[STOP_SENT]
    expect(screen.getByText(/Снять алерт/)).toBeInTheDocument();
    expect(screen.getByText("$12.50")).toBeInTheDocument();
  });

  // NORMAL: кнопка claim скрыта (claim вернул бы 409 — UX-гард)
  it("скрывает Снять алерт для NORMAL", async () => {
    getAdDetail.mockResolvedValue({
      fb_ad_id: "AD2",
      ad_name: "Норм",
      state: "NORMAL",
      snooze_until: null,
      metrics: {},
      recent_alerts: [],
      can_open_in_ads_manager: false,
    });
    renderAt("AD2");
    await waitFor(() => screen.getByText("Норм"));
    expect(screen.queryByText(/Снять алерт/)).toBeNull();
  });
});
