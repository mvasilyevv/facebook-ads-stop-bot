import type { Meta, StoryObj } from "@storybook/react";
import { Badge, type BadgeVariant } from "../src/components/ui/Badge";

const meta: Meta<typeof Badge> = {
  title: "UI/Badge",
  component: Badge,
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof Badge>;

export const Normal: Story = {
  args: { variant: "normal", children: "NORMAL" },
};

export const Warning: Story = {
  args: { variant: "warning", children: "WARN" },
};

export const Stop: Story = {
  args: { variant: "stop", children: "STOP" },
};

export const Claimed: Story = {
  args: { variant: "claimed", children: "CLAIM" },
};

export const Disabled: Story = {
  args: { variant: "disabled", children: "OFF" },
};

export const Success: Story = {
  args: { variant: "success", children: "OK" },
};

export const WithoutDot: Story = {
  args: { variant: "stop", withDot: false, children: "NO DOT" },
};

export const SmallSize: Story = {
  args: { variant: "warning", size: "sm", children: "WARN" },
};

export const AllVariants: Story = {
  render: () => {
    const variants: BadgeVariant[] = [
      "normal",
      "warning",
      "stop",
      "claimed",
      "disabled",
      "success",
      "info",
      "neutral",
      "pending",
      "running",
      "done",
      "failed",
      "retrying",
      "cancelled",
    ];
    return (
      <div className="flex flex-wrap gap-2">
        {variants.map((v) => (
          <Badge key={v} variant={v}>
            {v}
          </Badge>
        ))}
      </div>
    );
  },
};
