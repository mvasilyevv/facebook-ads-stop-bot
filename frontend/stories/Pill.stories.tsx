import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { FilterPill, Chip, Pill } from "../src/components/ui/Pill";

const meta: Meta = {
  title: "UI/Pill",
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof meta>;

export const FilterPills: Story = {
  render: () => {
    const [active, setActive] = useState<string[]>(["warning"]);
    const options = ["all", "warning", "stop", "claimed", "disabled"];
    return (
      <div className="flex gap-2 flex-wrap">
        {options.map((opt) => (
          <FilterPill
            key={opt}
            active={active.includes(opt)}
            onClick={() =>
              setActive((prev) =>
                prev.includes(opt) ? prev.filter((x) => x !== opt) : [...prev, opt],
              )
            }
          >
            {opt.toUpperCase()}
          </FilterPill>
        ))}
      </div>
    );
  },
};

export const Chips: Story = {
  render: () => {
    const [chips, setChips] = useState(["state = warning, stop", "offer = DRC", "search = tyver"]);
    return (
      <div className="flex gap-2 flex-wrap">
        {chips.map((c) => (
          <Chip key={c} onRemove={() => setChips((prev) => prev.filter((x) => x !== c))}>
            {c}
          </Chip>
        ))}
      </div>
    );
  },
};

export const DisplayPill: Story = {
  render: () => (
    <div className="flex gap-2">
      <Pill>CPL_HIGH</Pill>
      <Pill>SPEND_NO_EVT</Pill>
    </div>
  ),
};
