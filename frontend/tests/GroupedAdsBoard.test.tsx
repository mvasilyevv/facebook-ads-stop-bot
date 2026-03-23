import { render, screen, within } from "@testing-library/react";
import { GroupedAdsBoard } from "../src/components/GroupedAdsBoard";
import type { AdSummary } from "../src/types";

function buildAd(overrides: Partial<AdSummary>): AdSummary {
  return {
    fb_ad_id: "100",
    campaign_name: "Кампания Alpha",
    adset_name: "1",
    ad_name: "Ad Alpha",
    delivery_status: "ACTIVE",
    tracking_mode: "TRACKED",
    scope_presence: "IN_SCOPE",
    last_decision: "NO_ACTION",
    resolved_cpa_usd: "5.00",
    spend: "1.00",
    clicks: 1,
    cpc: "1.00",
    leads: 0,
    cost_per_lead: "0.00",
    registrations: 0,
    cost_per_registration: "0.00",
    deposits: 0,
    last_seen_at: "2026-03-22T10:00:00Z",
    ...overrides,
  };
}

// Проверяет, что board сортирует adset по возрастанию и ads внутри группы по расходу по убыванию.
test("GroupedAdsBoard sorts adsets ascending and ads by spend descending", () => {
  const ads = [
    buildAd({
      fb_ad_id: "3",
      adset_name: "3",
      ad_name: "Ad C low",
      spend: "0.10",
      last_seen_at: "2026-03-22T10:05:00Z",
    }),
    buildAd({
      fb_ad_id: "1",
      adset_name: "1",
      ad_name: "Ad A high",
      spend: "5.00",
      last_seen_at: "2026-03-22T10:10:00Z",
    }),
    buildAd({
      fb_ad_id: "2",
      adset_name: "1",
      ad_name: "Ad B low",
      delivery_status: "PAUSED",
      spend: "1.00",
      last_seen_at: "2026-03-22T10:11:00Z",
    }),
    buildAd({
      fb_ad_id: "4",
      adset_name: "2",
      ad_name: "Ad D medium",
      spend: "3.00",
      last_seen_at: "2026-03-22T10:12:00Z",
    }),
  ];

  const { container } = render(
    <GroupedAdsBoard
      ads={ads}
      emptyTitle="Пусто"
      emptyDescription="Пусто"
    />,
  );

  const adsetNames = Array.from(container.querySelectorAll(".ads-adset-block__head strong")).map(
    (node) => node.textContent,
  );
  expect(adsetNames).toEqual(["1", "2", "3"]);

  const firstAdsetBlock = container.querySelectorAll(".ads-adset-block")[0];
  expect(firstAdsetBlock).toBeTruthy();
  const firstAdTitles = Array.from(firstAdsetBlock.querySelectorAll(".ad-identity__title")).map(
    (node) => node.textContent,
  );
  expect(firstAdTitles).toEqual(["Ad A high", "Ad B low"]);
  expect(within(firstAdsetBlock as HTMLElement).getByText("2 объявлений")).toBeInTheDocument();
  expect(within(firstAdsetBlock as HTMLElement).getByText("на паузе 1")).toBeInTheDocument();
  expect(within(firstAdsetBlock as HTMLElement).getByText(/расход/)).toBeInTheDocument();

  expect(screen.getByText("4 объявлений")).toBeInTheDocument();
  expect(screen.getByText("активно 3")).toBeInTheDocument();
  expect(screen.getAllByText("на паузе 1")[0]).toBeInTheDocument();

  expect(screen.getAllByText("активно")[0]).toBeInTheDocument();
  expect(screen.getAllByText("действий не было")[0]).toBeInTheDocument();
  expect(screen.getByText("на паузе")).toBeInTheDocument();
});

// Проверяет, что карточка объявления показывает человекочитаемое описание последнего действия.
test("GroupedAdsBoard renders human readable last action summary", () => {
  render(
    <GroupedAdsBoard
      ads={[
        buildAd({
          fb_ad_id: "resume-1",
          ad_name: "Ad Resume",
          last_decision: "WOULD_RESUME",
          last_decision_reason: "Объявление снова безопасно для запуска",
          last_execution_state: "SUCCEEDED",
          last_action_message: "Объявление снова запущено",
          last_decision_at: "2026-03-22T10:15:00Z",
        }),
      ]}
      emptyTitle="Пусто"
      emptyDescription="Пусто"
    />,
  );

  expect(screen.getByText("авторезюм выполнен")).toBeInTheDocument();
  expect(screen.getByText("Объявление снова запущено")).toBeInTheDocument();
});
