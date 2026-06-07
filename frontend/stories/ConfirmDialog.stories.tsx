import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ConfirmDialog } from "../src/components/ui/ConfirmDialog";
import { Button } from "../src/components/ui/Button";

const meta: Meta<typeof ConfirmDialog> = {
  title: "UI/ConfirmDialog",
  component: ConfirmDialog,
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof ConfirmDialog>;

export const Danger: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button variant="danger" onClick={() => setOpen(true)}>Удалить кампанию</Button>
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title="Удалить кампанию?"
          description="Это действие необратимо. Все объявления будут удалены."
          confirmWord="УДАЛИТЬ"
          confirmLabel="Да, удалить"
          onConfirm={() => console.log("confirmed")}
        />
      </>
    );
  },
};

export const Approve: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button variant="primary" onClick={() => setOpen(true)}>Одобрить черновик</Button>
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title="Одобрить мутацию?"
          description="Пауза объявления UA17 | SP | MV | Krov будет отправлена в Meta API."
          confirmVariant="primary"
          confirmLabel="Одобрить"
          onConfirm={() => console.log("approved")}
        />
      </>
    );
  },
};
