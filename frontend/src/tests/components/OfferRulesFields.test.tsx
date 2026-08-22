import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { OfferRulesFields, DEFAULT_OFFER_RULES_VALUES } from "@/components/offers/OfferRulesFields";

vi.mock("@/lib/api/offers", () => ({
  useRulesPreview: () => ({ data: undefined, isLoading: false, isFetching: false }),
}));

describe("OfferRulesFields (new thresholds)", () => {
  it("shows default values for missing thresholds", () => {
    render(
      <OfferRulesFields
        values={DEFAULT_OFFER_RULES_VALUES}
        onChange={() => {}}
      />
    );
    // CPC % from CPA
    expect(screen.getByLabelText(/CPC: % от CPA/i)).toBeInTheDocument();
  });

  it("updates field and sends value, cleans field to null", async () => {
    const onChange = vi.fn();
    render(
      <OfferRulesFields
        values={{ ...DEFAULT_OFFER_RULES_VALUES, cpc_percent_of_cpa: "3" }}
        onChange={onChange}
      />
    );
    const input = screen.getByLabelText(/CPC: % от CPA/i);
    expect(input).toHaveValue("3");

    await userEvent.clear(input);
    expect(onChange).toHaveBeenCalledWith({ cpc_percent_of_cpa: "" }); // "" maps to null
  });
});
