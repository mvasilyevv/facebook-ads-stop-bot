import type { Meta, StoryObj } from "@storybook/react";
import { Input } from "../src/components/ui/Input";

const meta: Meta<typeof Input> = {
  title: "UI/Input",
  component: Input,
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof Input>;

export const Default: Story = {
  args: { placeholder: "Введите значение..." },
};

export const WithLabel: Story = {
  args: { label: "Название кампании", placeholder: "UA17 | SP | MV..." },
};

export const WithError: Story = {
  args: { label: "Бюджет", value: "abc", errorMessage: "Введите число" },
};

export const WithHelpText: Story = {
  args: { label: "Пороговое CPL", helpText: "Среднее за последние 7 дней" },
};

export const Disabled: Story = {
  args: { label: "Readonly", value: "read-only value", disabled: true },
};

export const Search: Story = {
  args: {
    type: "search",
    "aria-label": "Поиск объявлений",
    placeholder: "Поиск объявлений...",
  },
};

export const Sizes: Story = {
  render: () => (
    <div className="flex flex-col gap-3 w-64">
      <Input size="sm" placeholder="SM" />
      <Input size="md" placeholder="MD" />
      <Input size="lg" placeholder="LG" />
    </div>
  ),
};
