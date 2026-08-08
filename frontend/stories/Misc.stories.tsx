/**
 * Misc stories — Skeleton, EmptyState, ErrorState, Spinner, ProgressBar, Kbd, Card, Switch, Select, Toast.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { InboxIcon } from "lucide-react";
import { Skeleton } from "../src/components/ui/Skeleton";
import { EmptyState } from "../src/components/ui/EmptyState";
import { ErrorState } from "../src/components/ui/ErrorState";
import { Spinner, ProgressBar } from "../src/components/ui/Spinner";
import { Kbd } from "../src/components/ui/Kbd";
import { Card } from "../src/components/ui/Card";
import { Switch } from "../src/components/ui/Switch";
import { Select } from "../src/components/ui/Select";
import { ToastViewport, toast } from "../src/components/ui/Toast";
import { Button } from "../src/components/ui/Button";
import { useState } from "react";

const meta: Meta = {
  title: "UI/Misc",
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof meta>;

export const SkeletonVariants: Story = {
  render: () => (
    <div className="space-y-3 w-80">
      <Skeleton width="70%" />
      <Skeleton width="50%" height={10} />
      <Skeleton height={80} />
      {Array.from({ length: 3 }, (_, index) => (
        <Skeleton key={index} variant="row" />
      ))}
    </div>
  ),
};

export const EmptyStateFull: Story = {
  render: () => (
    <EmptyState
      icon={<InboxIcon size={32} strokeWidth={1} />}
      title="Нет активных алертов"
      description="Все объявления в пределах нормы. Бот продолжает мониторинг."
    />
  ),
};

export const ErrorStateFull: Story = {
  render: () => (
    <ErrorState
      error="Network Error: Failed to fetch /api/operator/ads"
      onRetry={() => console.log("retry")}
    />
  ),
};

export const Spinners: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <Spinner size={12} />
      <Spinner size={16} />
      <Spinner size={24} />
      <Spinner size={32} colorClass="border-success" />
    </div>
  ),
};

export const Progress: Story = {
  render: () => (
    <div className="space-y-3 w-60">
      <ProgressBar value={30} />
      <ProgressBar value={70} />
      <ProgressBar value={100} />
      <ProgressBar />
    </div>
  ),
};

export const KbdDemo: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Kbd>⌘</Kbd>
      <Kbd>K</Kbd>
      <span className="text-bg-9 text-[12px]">Открыть command palette</span>
    </div>
  ),
};

export const CardDemo: Story = {
  render: () => (
    <Card eyebrow="01 / OVERVIEW" title="Активные кампании" meta="12 open" className="w-80">
      <p className="text-bg-10 text-[13px]">Содержимое карточки.</p>
    </Card>
  ),
};

export const SwitchDemo: Story = {
  render: () => {
    const [on, setOn] = useState(false);
    return (
      <div className="space-y-3 w-72">
        <Switch
          checked={on}
          onChange={() => setOn((v) => !v)}
          label="Автоостановка"
          visualLabel="Автоостановка через Marketing API"
          description="Объявления с STOP отключаются автоматически"
        />
      </div>
    );
  },
};

export const SelectDemo: Story = {
  render: () => (
    <Select
      label="Фильтр по офферу"
      options={[
        { value: "", label: "Все офферы" },
        { value: "drc", label: "DRC" },
        { value: "cr2", label: "CR2" },
        { value: "gh", label: "GH" },
      ]}
    />
  ),
};

export const ToastDemo: Story = {
  render: () => (
    <>
      <ToastViewport />
      <div className="flex gap-2">
        <Button
          variant="secondary"
          onClick={() => toast.success("Сохранено", "Настройки обновлены")}
        >
          Success
        </Button>
        <Button variant="secondary" onClick={() => toast.info("Инфо", "Новый скан запущен")}>
          Info
        </Button>
        <Button
          variant="secondary"
          onClick={() => toast.warning("Внимание", "Достигнут лимит запросов")}
        >
          Warning
        </Button>
        <Button
          variant="danger"
          onClick={() => toast.error("Ошибка", "Не удалось подключиться к API")}
        >
          Error
        </Button>
      </div>
    </>
  ),
};
