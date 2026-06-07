import type { Meta, StoryObj } from "@storybook/react";
import { Badge, type BadgeVariant } from "../src/components/ui/Badge";
import { alertStateToBadgeVariant, taskStatusToBadgeVariant } from "@fb/shared";

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

export const Draft: Story = {
  args: { variant: "draft", children: "DRAFT" },
};

export const WithoutDot: Story = {
  args: { variant: "stop", withDot: false, children: "NO DOT" },
};

export const SmallSize: Story = {
  args: { variant: "warning", size: "sm", children: "WARN" },
};

// FSM-состояния через shared-хелперы
export const FsmAlertStates: Story = {
  render: () => {
    const states = ["normal", "warning_sent", "stop_sent", "claimed", "disabled"] as const;
    return (
      <div className="flex flex-wrap gap-2">
        {states.map((s) => (
          <Badge key={s} variant={alertStateToBadgeVariant(s)}>
            {s.replace("_", " ")}
          </Badge>
        ))}
      </div>
    );
  },
};

// Task-статусы через shared-хелперы
export const TaskStatuses: Story = {
  render: () => {
    const statuses = ["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED"] as const;
    return (
      <div className="flex flex-wrap gap-2">
        {statuses.map((s) => (
          <Badge key={s} variant={taskStatusToBadgeVariant(s) as BadgeVariant}>
            {s}
          </Badge>
        ))}
      </div>
    );
  },
};

export const AllVariants: Story = {
  render: () => {
    const variants: BadgeVariant[] = [
      "normal", "warning", "stop", "claimed", "disabled",
      "success", "info", "neutral", "pending", "running",
      "done", "failed", "retrying", "cancelled", "draft",
    ];
    return (
      <div className="flex flex-wrap gap-2">
        {variants.map((v) => (
          <Badge key={v} variant={v}>{v}</Badge>
        ))}
      </div>
    );
  },
};
