/**
 * Тесты PreviewBlock + buildCreateCampaignPreview + buildBulkPausePreview.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import {
  PreviewBlock,
  buildCreateCampaignPreview,
  buildBulkPausePreview,
} from "@/components/domain/drafts/PreviewBlock";

describe("PreviewBlock", () => {
  // create_campaign — секции Campaign / Adset / Creative присутствуют
  it("buildCreateCampaignPreview показывает секцию Campaign", () => {
    const props = buildCreateCampaignPreview({
      campaign_name: "DRC_CR2 | UA | MV | 28.05",
      objective: "OUTCOME_LEADS",
      status: "PAUSED",
      adset_name: "UA | LAL1",
      daily_budget: 150,
      optimization_goal: "LEAD_GENERATION",
      geo: "UA",
    });
    render(<PreviewBlock {...props} />);
    // Заголовки секций
    expect(screen.getByText("01 · Campaign")).toBeInTheDocument();
    expect(screen.getByText("02 · Adset")).toBeInTheDocument();
  });

  // create_campaign — название кампании отображается
  it("buildCreateCampaignPreview показывает campaign_name", () => {
    const props = buildCreateCampaignPreview({
      campaign_name: "DRC_CR2 | UA | MV | 28.05",
    });
    render(<PreviewBlock {...props} />);
    expect(screen.getByText("DRC_CR2 | UA | MV | 28.05")).toBeInTheDocument();
  });

  // create_campaign — бюджет в долларах
  it("buildCreateCampaignPreview показывает daily_budget", () => {
    const props = buildCreateCampaignPreview({ daily_budget: 150 });
    render(<PreviewBlock {...props} />);
    expect(screen.getByText("$150.00")).toBeInTheDocument();
  });

  // bulk-pause — показывает bullet-list affected ads
  it("buildBulkPausePreview показывает Affected ads", () => {
    const ids = ["120211984573_001", "120211984573_002", "120211984573_003"];
    const props = buildBulkPausePreview({
      object_ids: ids,
      offer_code: "DRC_CR2",
      action: "pause",
    });
    render(<PreviewBlock {...props} />);
    expect(screen.getByText(/Affected ads · 3/i)).toBeInTheDocument();
    ids.forEach((id) => expect(screen.getByText(id)).toBeInTheDocument());
  });

  // bulk-pause — секция Selector c offer_code
  it("buildBulkPausePreview показывает offer_code в Selector", () => {
    const props = buildBulkPausePreview({
      offer_code: "DRC_CR2",
      object_ids: ["001"],
    });
    render(<PreviewBlock {...props} />);
    expect(screen.getByText("Selector")).toBeInTheDocument();
    expect(screen.getByText('"DRC_CR2"')).toBeInTheDocument();
  });

  // Пустой PreviewBlock — fallback
  it("пустой PreviewBlock показывает —", () => {
    render(<PreviewBlock sections={[]} bullets={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
