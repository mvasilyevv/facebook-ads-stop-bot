import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Drawer } from "../src/components/ui/Drawer";
import { Button } from "../src/components/ui/Button";

const meta: Meta<typeof Drawer> = {
  title: "UI/Drawer",
  component: Drawer,
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof Drawer>;

export const Default: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>Открыть Drawer</Button>
        <Drawer
          open={open}
          onOpenChange={setOpen}
          eyebrow="06 · AD DETAIL · TIMELINE"
          title="UA17 | SP | MV | Krov | 24.03"
          description="ad_id: 120206..._user"
          footer={
            <>
              <span className="text-[12px] text-bg-9">Последний скан: 14:32:18</span>
              <div className="flex gap-2">
                <Button variant="ghost">Snooze</Button>
                <Button variant="danger">Отключить</Button>
              </div>
            </>
          }
        >
          <div className="space-y-4">
            <p className="text-bg-10 text-[13px]">Содержимое drawer — метрики, таймлайн и т.д.</p>
            <div className="h-40 bg-bg-2 border border-bg-5 flex items-center justify-center text-bg-8 text-[12px]">
              Chart placeholder
            </div>
          </div>
        </Drawer>
      </>
    );
  },
};

export const Narrow: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>480px Drawer</Button>
        <Drawer open={open} onOpenChange={setOpen} title="Настройки правил" width={480}>
          <p className="text-bg-10 text-[13px]">Узкий drawer для форм настроек.</p>
        </Drawer>
      </>
    );
  },
};
