import type { Meta, StoryObj } from "@storybook/react";
import { KPICard, KPIStrip } from "@/components/data/KPICard";

const meta: Meta<typeof KPICard> = {
  title: "Data/KPICard",
  component: KPICard,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof KPICard>;

export const Active: Story = {
  args: {
    label: "Active",
    value: "247",
    hint: "today scan",
    trend: { value: "−4", direction: "down" },
    variant: "muted",
  },
};

export const Warning: Story = {
  args: {
    label: "Warning",
    value: "12",
    hint: "now",
    trend: { value: "+3", direction: "up" },
    variant: "warning",
  },
};

export const Strip: Story = {
  render: () => (
    <div className="w-[1100px]">
      <KPIStrip>
        <KPICard
          label="Active"
          value="247"
          hint="today scan"
          trend={{ value: "−4", direction: "down" }}
          variant="muted"
        />
        <KPICard
          label="Warning"
          value="12"
          hint="now"
          trend={{ value: "+3", direction: "up" }}
          variant="warning"
        />
        <KPICard
          label="Stop"
          value="4"
          hint="now"
          trend={{ value: "−1", direction: "down" }}
          variant="danger"
        />
        <KPICard
          label="Disabled"
          value="89"
          hint="today total"
          trend={{ value: "+12", direction: "up" }}
          variant="success"
        />
      </KPIStrip>
    </div>
  ),
};
