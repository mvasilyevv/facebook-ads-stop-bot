import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Tabs, TabsList, TabsContent, type TabItem } from "../src/components/ui/Tabs";

const meta: Meta<typeof Tabs> = {
  title: "UI/Tabs",
  component: Tabs,
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof Tabs>;

const ITEMS: TabItem[] = [
  { value: "overview", label: "Обзор", count: 12 },
  { value: "alerts", label: "Алерты", count: 3 },
  { value: "tasks", label: "Задачи" },
  { value: "disabled", label: "Disabled", disabled: true },
];

export const Underline: Story = {
  render: () => {
    const [tab, setTab] = useState("overview");
    return (
      <Tabs value={tab} onValueChange={setTab} variant="underline">
        <TabsList items={ITEMS} variant="underline" />
        {ITEMS.filter((i) => !i.disabled).map((i) => (
          <TabsContent key={i.value} value={i.value} className="pt-4">
            <p className="text-bg-10 text-[13px]">Контент: {String(i.label)}</p>
          </TabsContent>
        ))}
      </Tabs>
    );
  },
};

export const Segmented: Story = {
  render: () => {
    const items: TabItem[] = [
      { value: "all", label: "Все" },
      { value: "active", label: "Активные" },
      { value: "paused", label: "Пауза" },
    ];
    const [tab, setTab] = useState("all");
    return (
      <Tabs value={tab} onValueChange={setTab} variant="segmented">
        <TabsList items={items} variant="segmented" />
        {items.map((i) => (
          <TabsContent key={i.value} value={i.value} className="pt-4">
            <p className="text-bg-10 text-[13px]">Вкладка: {String(i.label)}</p>
          </TabsContent>
        ))}
      </Tabs>
    );
  },
};
