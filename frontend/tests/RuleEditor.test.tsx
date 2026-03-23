import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RuleEditor } from "../src/components/RuleEditor";
import type { RuleItem } from "../src/types";

const BASE_RULE: RuleItem = {
  id: "rule-1",
  code: "stop_high_cpc",
  title: "Стоп по дорогому клику",
  description: "Останавливает объявление, если стоимость клика стала слишком высокой.",
  is_enabled: true,
  priority: 10,
  cpa_multiplier: "0.0160",
  updated_at: "2026-03-23T12:00:00.000Z",
};

describe("RuleEditor", () => {
  // Проверяет, что редактор показывает денежный эквивалент дробного процента и сохраняет точное значение.
  it("показывает денежный порог и сохраняет дробный процент", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <RuleEditor
        rule={BASE_RULE}
        offerPreviews={[
          {
            offerId: "offer-1",
            offerName: "CR2",
            offerCode: "DRC_CR2",
            cpaUsd: 5,
          },
        ]}
        onSave={onSave}
      />,
    );

    expect(screen.getByText("Сейчас это 0,08 $")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("spinbutton"), {
      target: { value: "1.75" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить правило" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        is_enabled: true,
        cpa_multiplier: "0.0175",
      }),
    );
  });
});
