import type { Meta, StoryObj } from "@storybook/react";
import { DraftCard } from "@/components/domain/DraftCard";

const meta: Meta<typeof DraftCard> = {
  title: "Domain/DraftCard",
  component: DraftCard,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof DraftCard>;

export const PauseAd: Story = {
  args: {
    taskType: "meta_api / pause_ad",
    createdAt: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
    requestedBy: "markvasilev",
    summary: "Will PAUSE 1 ad",
    diff: [
      { key: "ad_id", target: "1202118765432" },
      { key: "ad_name", target: "CR2 | DRC | MV | Tyver | 25.03" },
      {
        key: "state",
        current: "ACTIVE",
        target: "PAUSED",
        highlight: true,
      },
    ],
    reason:
      "Spend $234 / CPL $42 over threshold for last 2 hours. Frequency 4.8 above limit. Recommend pause to prevent further losses.",
    expiresAt: new Date(Date.now() + 23 * 3600 * 1000 + 47 * 60 * 1000).toISOString(),
    canApprove: true,
  },
};

export const BudgetChange: Story = {
  args: {
    taskType: "meta_api / set_adset_budget",
    createdAt: new Date(Date.now() - 3600 * 1000).toISOString(),
    requestedBy: "markvasilev",
    summary: "Will UPDATE adset budget",
    diff: [
      { key: "adset_id", target: "1202118876655" },
      {
        key: "daily_budget",
        current: "$200.00",
        target: "$350.00 (+75%)",
        highlight: true,
      },
      { key: "safety_cap", target: "$100,000 (under)" },
    ],
    expiresAt: new Date(Date.now() + 22 * 3600 * 1000).toISOString(),
  },
};

export const NotOwner: Story = {
  args: {
    ...PauseAd.args!,
    canApprove: false,
    approveDisabledReason: "Only owner can approve this draft. Created by @markvasilev.",
  },
};
