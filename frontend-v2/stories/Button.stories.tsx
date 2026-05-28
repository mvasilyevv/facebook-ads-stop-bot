import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "@/components/ui/Button";
import { Plus, Trash2 } from "lucide-react";

const meta: Meta<typeof Button> = {
  title: "UI/Button",
  component: Button,
  parameters: { layout: "centered" },
  argTypes: {
    variant: {
      control: "select",
      options: ["primary", "secondary", "ghost", "danger", "link"],
    },
    size: { control: "select", options: ["xs", "sm", "md", "lg"] },
  },
};
export default meta;

type Story = StoryObj<typeof Button>;

export const Primary: Story = {
  args: { variant: "primary", size: "md", children: "Scan now" },
};

export const Secondary: Story = {
  args: { variant: "secondary", size: "md", children: "Settings" },
};

export const Ghost: Story = {
  args: { variant: "ghost", size: "md", children: "View all" },
};

export const Danger: Story = {
  args: {
    variant: "danger",
    size: "md",
    children: "Disable ad",
    leftIcon: <Trash2 size={14} />,
  },
};

export const WithIcon: Story = {
  args: {
    variant: "primary",
    size: "md",
    children: "New offer",
    leftIcon: <Plus size={14} />,
  },
};

export const Loading: Story = {
  args: { variant: "primary", size: "md", children: "Saving...", loading: true },
};

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button variant="secondary" size="xs">
        xs
      </Button>
      <Button variant="secondary" size="sm">
        sm
      </Button>
      <Button variant="secondary" size="md">
        md
      </Button>
      <Button variant="secondary" size="lg">
        lg
      </Button>
    </div>
  ),
};
