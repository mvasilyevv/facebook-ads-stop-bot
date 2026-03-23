import { fireEvent, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import AdsPage from "../../src/pages/AdsPage";
import type { AdSummary } from "../../src/types";
import { server } from "../test-server";
import { renderWithRouter } from "../test-utils";

function buildAdSummary(overrides: Partial<AdSummary>): AdSummary {
  return {
    fb_ad_id: "120241420000001001",
    campaign_name: "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
    adset_name: "1",
    ad_name: "DRC_CR2_CR001",
    delivery_status: "PAUSED",
    tracking_mode: "TRACKED",
    scope_presence: "IN_SCOPE",
    last_seen_at: "2026-03-22T11:20:20.912202Z",
    last_decision: "NO_ACTION",
    resolved_cpa_usd: "5.00",
    spend: "0.00",
    clicks: 0,
    cpc: "0.00",
    leads: 0,
    cost_per_lead: "0.00",
    registrations: 0,
    cost_per_registration: "0.00",
    deposits: 0,
    ...overrides,
  };
}

describe("AdsPage", () => {
  // Проверяет, что adset-блоки сортируются по возрастанию имени, stale-бейдж виден, а tracked-текст исчез.
  it("сортирует adset по возрастанию и показывает stale-бейдж для не увиденного объявления", async () => {
    server.use(
      http.get(
        "*/ads",
        () =>
          HttpResponse.json([
            buildAdSummary({
              fb_ad_id: "120241420000001301",
              adset_name: "3",
              ad_name: "DRC_CR2_CR301",
              spend: "0.43",
              scope_presence: "NOT_SEEN_THIS_SCAN",
            }),
            buildAdSummary({ fb_ad_id: "120241420000001101", adset_name: "1", ad_name: "DRC_CR2_CR101", spend: "1.54" }),
            buildAdSummary({ fb_ad_id: "120241420000001201", adset_name: "2", ad_name: "DRC_CR2_CR201", spend: "0.57" }),
          ]),
      ),
    );

    const { container } = renderWithRouter(<AdsPage />);

    expect(await screen.findByText("DRC_CR2_CR101")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR201")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR301")).toBeInTheDocument();

    const adsetHeads = Array.from(container.querySelectorAll(".ads-adset-block__head strong")).map((node) =>
      node.textContent?.trim(),
    );
    expect(adsetHeads.slice(0, 3)).toEqual(["1", "2", "3"]);
    expect(screen.queryByText(/^tracked$/i)).not.toBeInTheDocument();
    expect(screen.getByText("нет в последнем скане")).toBeInTheDocument();

    const board = container.querySelector(".ads-board");
    expect(board).not.toBeNull();
    expect(within(board as HTMLElement).getAllByLabelText(/Объявление .* отслеживается|Объявление отслеживается/i)).toHaveLength(3);
  });

  // Проверяет, что один adset показывает все четыре объявления без искусственного лимита карточек.
  it("не обрезает четвертую карточку внутри одного adset", async () => {
    server.use(
      http.get(
        "*/ads",
        () =>
          HttpResponse.json([
            buildAdSummary({ fb_ad_id: "120241420000002101", ad_name: "DRC_CR2_CR005", spend: "1.54" }),
            buildAdSummary({ fb_ad_id: "120241420000002102", ad_name: "DRC_CR2_CR006", spend: "0.43" }),
            buildAdSummary({ fb_ad_id: "120241420000002103", ad_name: "DRC_CR2_CR007", spend: "0.18" }),
            buildAdSummary({
              fb_ad_id: "120241420000002104",
              ad_name: "DRC_CR2_CR008",
              delivery_status: "ACTIVE",
              scope_presence: "NOT_SEEN_THIS_SCAN",
              spend: "0.00",
            }),
          ]),
      ),
    );

    const { container } = renderWithRouter(<AdsPage />);

    expect(await screen.findByText("DRC_CR2_CR005")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR006")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR007")).toBeInTheDocument();
    expect(screen.getByText("DRC_CR2_CR008")).toBeInTheDocument();

    const adsetBlocks = container.querySelectorAll(".ads-adset-block");
    expect(adsetBlocks).toHaveLength(1);
    expect(within(adsetBlocks[0] as HTMLElement).getAllByRole("article")).toHaveLength(4);
  });

  // Проверяет, что быстрый фильтр "внимание" оставляет только объявления с проблемным состоянием.
  it("фильтрует объявления по быстрому статусу внимания", async () => {
    server.use(
      http.get(
        "*/ads",
        () =>
          HttpResponse.json([
            buildAdSummary({
              fb_ad_id: "120241420000003001",
              ad_name: "DRC_CR2_CR_ATTENTION",
              scope_presence: "NOT_SEEN_THIS_SCAN",
            }),
            buildAdSummary({
              fb_ad_id: "120241420000003002",
              ad_name: "DRC_CR2_CR_OK",
              delivery_status: "ACTIVE",
              scope_presence: "IN_SCOPE",
            }),
          ]),
      ),
    );

    renderWithRouter(<AdsPage />);

    expect(await screen.findByText("DRC_CR2_CR_ATTENTION")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /внимание/i }));

    expect(screen.getByText("DRC_CR2_CR_ATTENTION")).toBeInTheDocument();
    expect(screen.queryByText("DRC_CR2_CR_OK")).not.toBeInTheDocument();
  });
});
