/**
 * Тесты AdsTable (канон ads-web.jsx) — рендер строк, флаги, selection, открытие.
 * Виртуализатор замокан, чтобы jsdom (без layout) показывал все строки.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import type { AdSnapshot } from "@fb/shared";

// Мок виртуализатора: вернуть все элементы.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_, i) => ({ index: i, start: i * 44, size: 44 })),
    getTotalSize: () => count * 44,
  }),
}));

import { AdsTable } from "@/components/domain/ads/AdsTable";

function makeAd(id: string, overrides: Partial<AdSnapshot> = {}): AdSnapshot {
  return {
    fb_ad_id: id,
    internal_id: `u-${id}`,
    ad_name: `CR2 | DRC | MV | GH | ${id}`,
    offer_code: "DRC",
    alert_state: "normal",
    is_active: true,
    stop_rule_codes: [],
    warning_rule_codes: [],
    ...overrides,
  } as AdSnapshot;
}

const baseProps = {
  selected: new Set<string>(),
  cursor: -1,
  rowHeight: 44,
  onToggleSelect: vi.fn(),
  onOpen: vi.fn(),
};

describe("AdsTable", () => {
  // Header-лейблы колонок (eyebrow).
  it("рендерит header колонок", () => {
    render(<AdsTable {...baseProps} rows={[makeAd("1")]} />);
    expect(screen.getByText("AD")).toBeInTheDocument();
    expect(screen.getByText("SPEND")).toBeInTheDocument();
    expect(screen.getByText("ROAS")).toBeInTheDocument();
  });

  // Рендерит строки с именем и offer.
  it("рендерит строки объявлений", () => {
    render(<AdsTable {...baseProps} rows={[makeAd("1"), makeAd("2")]} />);
    expect(screen.getByText("CR2 | DRC | MV | GH | 1")).toBeInTheDocument();
    expect(screen.getByText("CR2 | DRC | MV | GH | 2")).toBeInTheDocument();
  });

  // Родитель: контекст кампании + хвост адсета («отец») — различает дубли по адсету.
  it("показывает контекст кампании и хвост адсета под названием", () => {
    render(
      <AdsTable
        {...baseProps}
        rows={[
          makeAd("1", {
            offer_code: "DRC",
            campaign_name: "GH_CR | 18.06",
            adset_name: "adset-ios",
          }),
        ]}
      />,
    );
    // Контекст кампании (через « · ») и различающий хвост адсета — отдельными сегментами.
    expect(screen.getByText("GH_CR · 18.06")).toBeInTheDocument();
    expect(screen.getByText("adset-ios")).toBeInTheDocument();
  });

  // Общий префикс campaign∩adset (owner/тип) сворачивается: у адсета только хвост.
  it("сворачивает общий префикс кампании и адсета", () => {
    render(
      <AdsTable
        {...baseProps}
        rows={[
          makeAd("1", {
            offer_code: "GH_CR",
            campaign_name: "MV | GH_CR | static | adset.pro | 18.06",
            adset_name: "MV | GH_CR | static | s1",
          }),
        ]}
      />,
    );
    // offer_code (GH_CR) выкинут; общий префикс MV·static — у адсета остаётся «s1».
    expect(screen.getByText("MV · static · adset.pro · 18.06")).toBeInTheDocument();
    expect(screen.getByText("s1")).toBeInTheDocument();
  });

  // ROAS всегда «—» (нет в API).
  it("ROAS показывает «—» (нет в API-схеме)", () => {
    render(
      <AdsTable
        {...baseProps}
        rows={[makeAd("1", { metrics: { cycle_ts: "t", spend: "100", leads: 5 } })]}
      />,
    );
    // Хотя бы один «—×» отсутствует — ROAS рендерится как «—».
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  // CPL>30 → danger-класс (text-danger).
  it("CPL > 30 окрашивает значение в danger", () => {
    render(
      <AdsTable
        {...baseProps}
        rows={[
          makeAd("1", {
            metrics: { cycle_ts: "t", spend: "500", cost_per_lead: "42.0", leads: 10 },
          }),
        ]}
      />,
    );
    const cpl = screen.getByText("$42.0");
    expect(cpl.className).toContain("text-danger");
  });

  // Клик по строке → onOpen.
  it("клик по строке вызывает onOpen", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<AdsTable {...baseProps} rows={[makeAd("1")]} onOpen={onOpen} />);
    await user.click(screen.getByText("CR2 | DRC | MV | GH | 1"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("Enter на строке открывает детали с клавиатуры", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<AdsTable {...baseProps} rows={[makeAd("1")]} onOpen={onOpen} />);

    screen.getByRole("row", { name: /Открыть подробности/ }).focus();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  // Клик по чекбоксу → onToggleSelect, не всплывает к onOpen.
  it("клик по чекбоксу вызывает onToggleSelect (не onOpen)", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onToggleSelect = vi.fn();
    render(
      <AdsTable
        {...baseProps}
        rows={[makeAd("1")]}
        onOpen={onOpen}
        onToggleSelect={onToggleSelect}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Выбрать CR2/ }));
    expect(onToggleSelect).toHaveBeenCalledWith("1");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("Enter на кнопке выбора не открывает drawer", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onToggleSelect = vi.fn();
    render(
      <AdsTable
        {...baseProps}
        rows={[makeAd("1")]}
        onOpen={onOpen}
        onToggleSelect={onToggleSelect}
      />,
    );

    screen.getByRole("button", { name: /Выбрать CR2/ }).focus();
    await user.keyboard("{Enter}");

    expect(onToggleSelect).toHaveBeenCalledWith("1");
    expect(onOpen).not.toHaveBeenCalled();
  });

  // Выбранная строка: toggle-кнопка aria-pressed=true.
  it("выбранная строка имеет aria-pressed=true", () => {
    render(<AdsTable {...baseProps} rows={[makeAd("1")]} selected={new Set(["1"])} />);
    expect(screen.getByRole("button", { name: /Выбрать CR2/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  // Мульти-кабинет: колонка CAB показывает хвост ID, полный — в title.
  it("колонка CAB показывает хвост ID кабинета", () => {
    const ad = makeAd("1", { ad_account_id: "1234567890" } as Partial<AdSnapshot>);
    render(<AdsTable {...baseProps} rows={[ad]} />);
    expect(screen.getByText("CAB")).toBeInTheDocument();
    expect(screen.getByTitle("Кабинет 1234567890")).toHaveTextContent("…7890");
  });

  // Кнопка «Открыть в Ads Manager» удалена — ссылок в строке нет.
  it("ссылок в строке нет (кнопка Ads Manager убрана)", () => {
    const ad = makeAd("1", { ad_account_id: "555" } as Partial<AdSnapshot>);
    render(<AdsTable {...baseProps} rows={[ad]} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  // Превью крео: если creative_thumb_url задан — рендерится img (data-testid=creative-thumb) с этим src.
  it("рендерит img с creative_thumb_url при наличии", () => {
    const thumbUrl = "https://example.com/thumb.jpg";
    const ad = makeAd("1", { creative_thumb_url: thumbUrl } as Partial<AdSnapshot>);
    render(<AdsTable {...baseProps} rows={[ad]} />);
    // img имеет alt="" → role=presentation, ищем по data-testid.
    const img = screen.getByTestId("creative-thumb");
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", thumbUrl);
  });

  // Превью крео: если creative_thumb_url не задан — рендерится geo-плейсхолдер (GeoThumb), img отсутствует.
  it("рендерит geo-плейсхолдер (GeoThumb) когда creative_thumb_url равен null", () => {
    const ad = makeAd("1", { creative_thumb_url: null } as Partial<AdSnapshot>);
    render(<AdsTable {...baseProps} rows={[ad]} />);
    // GeoThumb рендерит span с гео-меткой — img.creative-thumb отсутствует.
    expect(screen.queryByTestId("creative-thumb")).not.toBeInTheDocument();
  });

  // Превью крео: если creative_thumb_url не задан вовсе — geo-плейсхолдер тоже.
  it("рендерит geo-плейсхолдер когда creative_thumb_url отсутствует (undefined)", () => {
    const ad = makeAd("1");
    render(<AdsTable {...baseProps} rows={[ad]} />);
    expect(screen.queryByTestId("creative-thumb")).not.toBeInTheDocument();
  });
});
