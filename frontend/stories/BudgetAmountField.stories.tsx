import type { Meta, StoryObj } from "@storybook/react";
import { useState, type ComponentProps } from "react";

import { BudgetAmountField } from "@/components/domain/ads/AdsetDuplicateAction";

function BudgetStory(args: ComponentProps<typeof BudgetAmountField>) {
  const [cents, setCents] = useState(args.cents);
  return <BudgetAmountField {...args} cents={cents} onCents={setCents} />;
}

const meta = {
  title: "Ads/Budget amount field",
  component: BudgetAmountField,
  parameters: {
    layout: "centered",
  },
  decorators: [
    (Story) => (
      <div className="w-[520px] max-w-[calc(100vw-32px)]">
        <Story />
      </div>
    ),
  ],
  render: (args) => <BudgetStory {...args} />,
} satisfies Meta<typeof BudgetAmountField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AboSixAdsets: Story = {
  args: {
    cents: 10_000,
    currency: "USD",
    budgetLevel: "ABO",
    unitCount: 6,
    onCents: () => undefined,
  },
};

export const CboThreeCampaigns: Story = {
  args: {
    cents: 20_000,
    currency: "USD",
    budgetLevel: "CBO",
    unitCount: 3,
    onCents: () => undefined,
  },
};
