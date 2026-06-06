import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Checkbox, type CheckboxState } from "../src/components/ui/Checkbox";

const meta: Meta<typeof Checkbox> = {
  title: "UI/Checkbox",
  component: Checkbox,
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof Checkbox>;

export const Unchecked: Story = {
  args: { checked: false, label: "Выбрать" },
};

export const Checked: Story = {
  args: { checked: true, label: "Выбрано" },
};

export const Indeterminate: Story = {
  args: { checked: "indeterminate", label: "Частично" },
};

export const Disabled: Story = {
  args: { checked: false, disabled: true, label: "Недоступно" },
};

// Интерактивный tri-state
export const Interactive: Story = {
  render: () => {
    const [state, setState] = useState<CheckboxState>("indeterminate");
    return (
      <div className="flex flex-col gap-3">
        <Checkbox checked={state} onChange={(v) => setState(v)} label="Интерактивный" />
        <p className="text-[12px] text-bg-9">Состояние: {String(state)}</p>
      </div>
    );
  },
};

// Список с индикатором "выбрать все"
export const SelectAll: Story = {
  render: () => {
    const [selected, setSelected] = useState<Set<number>>(new Set([1]));
    const items = [1, 2, 3, 4];
    const allChecked = selected.size === items.length;
    const someChecked = selected.size > 0 && !allChecked;
    const headerState: CheckboxState = allChecked ? true : someChecked ? "indeterminate" : false;

    function toggleAll(v: boolean) {
      setSelected(v ? new Set(items) : new Set());
    }
    function toggle(id: number, v: boolean) {
      setSelected((prev) => {
        const next = new Set(prev);
        v ? next.add(id) : next.delete(id);
        return next;
      });
    }

    return (
      <div className="space-y-2">
        <Checkbox checked={headerState} onChange={toggleAll} label="Выбрать все" />
        <div className="ml-6 space-y-1">
          {items.map((id) => (
            <Checkbox
              key={id}
              checked={selected.has(id)}
              onChange={(v) => toggle(id, v)}
              label={`Объявление #${id}`}
            />
          ))}
        </div>
      </div>
    );
  },
};
