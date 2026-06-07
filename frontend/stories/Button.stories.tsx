import type { Meta, StoryObj } from "@storybook/react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../src/components/ui/Button";

const meta: Meta<typeof Button> = {
  title: "UI/Button",
  component: Button,
  parameters: { layout: "centered" },
  argTypes: {
    variant: {
      control: "select",
      options: ["primary", "secondary", "danger", "ghost", "ghost-danger", "link"],
    },
    size: { control: "select", options: ["xs", "sm", "md", "lg", "icon"] },
  },
};

export default meta;
type Story = StoryObj<typeof Button>;

export const Primary: Story = {
  args: { variant: "primary", children: "Создать кампанию" },
};

export const Secondary: Story = {
  args: { variant: "secondary", children: "Отмена" },
};

export const Danger: Story = {
  args: { variant: "danger", children: "Удалить" },
};

export const Ghost: Story = {
  args: { variant: "ghost", children: "Подробнее" },
};

export const GhostDanger: Story = {
  args: { variant: "ghost-danger", children: "Отключить" },
};

export const WithLeftIcon: Story = {
  args: { variant: "primary", leftIcon: <Plus size={14} />, children: "Добавить" },
};

export const WithRightIcon: Story = {
  args: { variant: "secondary", rightIcon: <Trash2 size={14} />, children: "Удалить" },
};

export const Loading: Story = {
  args: { variant: "primary", loading: true, children: "Сохраняем..." },
};

export const Disabled: Story = {
  args: { variant: "primary", disabled: true, children: "Недоступно" },
};

export const IconOnly: Story = {
  args: { variant: "ghost", size: "icon", "aria-label": "Добавить", children: <Plus size={14} /> },
};

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button variant="secondary" size="xs">XS</Button>
      <Button variant="secondary" size="sm">SM</Button>
      <Button variant="secondary" size="md">MD</Button>
      <Button variant="secondary" size="lg">LG</Button>
    </div>
  ),
};

export const AllVariants: Story = {
  render: () => (
    <div className="flex flex-wrap gap-3">
      {(["primary", "secondary", "danger", "ghost", "ghost-danger"] as const).map((v) => (
        <Button key={v} variant={v}>{v}</Button>
      ))}
    </div>
  ),
};
