import type { Meta, StoryObj } from "@storybook/react";
import { useMemo } from "react";
import { Table } from "@/components/data/Table";
import { Badge, alertStateToBadge } from "@/components/ui/Badge";
import { formatSpend, truncateAdId } from "@/lib/utils/format";
import type { ColumnDef } from "@tanstack/react-table";

interface Row {
  fb_ad_id: string;
  ad_name: string;
  offer: string;
  state: string;
  spend: number;
  cpl: number;
}

const meta: Meta = {
  title: "Data/Table",
  parameters: { layout: "padded" },
};
export default meta;

const SAMPLE: Row[] = Array.from({ length: 40 }, (_, i) => ({
  fb_ad_id: `1202118765432${String(i).padStart(2, "0")}`,
  ad_name: `${["CR2", "UA17", "DRC", "PT_BR"][i % 4]} | ${["DRC", "SP", "MV"][i % 3]} | Tyver | ${10 + i}.03`,
  offer: ["DRC_CR2", "UA17_MV", "PT_DRC", "NV_KZ"][i % 4] ?? "",
  state: ["normal", "warning_sent", "stop_sent", "claimed", "disabled"][i % 5] ?? "normal",
  spend: 50 + i * 12.34,
  cpl: 8 + i * 0.7,
}));

function useColumns(): ColumnDef<Row, unknown>[] {
  return useMemo(
    () => [
      {
        accessorKey: "fb_ad_id",
        header: "ID",
        cell: (info) => (
          <span className="font-numeric text-bg-9 text-[12px]">
            {truncateAdId(info.getValue() as string)}
          </span>
        ),
        size: 120,
      },
      { accessorKey: "ad_name", header: "Ad name" },
      { accessorKey: "offer", header: "Offer", size: 100 },
      {
        accessorKey: "state",
        header: "State",
        cell: (info) => {
          const v = info.getValue() as string;
          return <Badge variant={alertStateToBadge(v)}>{v}</Badge>;
        },
        size: 120,
      },
      {
        accessorKey: "spend",
        header: () => <span className="text-right block">Spend</span>,
        cell: (info) => (
          <span className="font-numeric tabular-nums text-right block">
            {formatSpend(info.getValue() as number)}
          </span>
        ),
        size: 100,
      },
      {
        accessorKey: "cpl",
        header: () => <span className="text-right block">CPL</span>,
        cell: (info) => (
          <span className="font-numeric tabular-nums text-right block">
            {formatSpend(info.getValue() as number)}
          </span>
        ),
        size: 100,
      },
    ],
    [],
  );
}

export const Default: StoryObj = {
  render: () => {
    const columns = useColumns();
    return <Table data={SAMPLE} columns={columns} height={500} />;
  },
};

export const Empty: StoryObj = {
  render: () => {
    const columns = useColumns();
    return (
      <Table
        data={[]}
        columns={columns}
        height={300}
        virtualized={false}
        emptyState={<span className="text-bg-9">No warnings — system is calm.</span>}
      />
    );
  },
};

export const Loading: StoryObj = {
  render: () => {
    const columns = useColumns();
    return <Table data={[]} columns={columns} virtualized={false} loading height={300} />;
  },
};
