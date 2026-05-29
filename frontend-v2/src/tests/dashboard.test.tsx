// Тесты: Dashboard-секции (KPI strip, task queue, incidents) — рендер данных,
// empty- и error-состояния. Покрывают presentational-композиты страницы без
// router/QueryClient — данные и флаги состояний приходят через props.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { KpiSection } from "@/components/dashboard/KpiSection";
import { TaskQueueCard } from "@/components/dashboard/TaskQueueCard";
import { IncidentsCard } from "@/components/dashboard/IncidentsCard";
import type { DashboardStats, TaskQueueRow, Incident } from "@/lib/types/api";

const STATS: DashboardStats = {
  total_ads_monitored: 247,
  ads_in_normal: 220,
  ads_in_warning: 12,
  ads_in_stop: 4,
  ads_in_claimed: 2,
  ads_in_disabled: 9,
  active_incidents: 16,
  last_scan_at: "2026-05-28T14:32:00Z",
  last_scan_outcome: "ok",
  scans_today: 4287,
  scans_today_with_errors: 0,
  observer_status: "running",
  pending_disable_tasks: 12,
  pending_enable_tasks: 3,
  failed_tasks_24h: 0,
};

describe("Dashboard · KpiSection", () => {
  // Тест: при наличии stats показывает 4 KPI с правильными числами и лейблами.
  it("рендерит 4 KPI с числами из stats", () => {
    render(<KpiSection stats={STATS} isLoading={false} isError={false} />);
    expect(screen.getByText("Объявлений под наблюдением")).toBeInTheDocument();
    expect(screen.getByText("247")).toBeInTheDocument();
    expect(screen.getByText("Предупреждений")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("В стопе")).toBeInTheDocument();
    expect(screen.getByText("Активные инциденты")).toBeInTheDocument();
    expect(screen.getByText("16")).toBeInTheDocument();
  });

  // Тест: loading рисует skeleton-плейсхолдеры (role=status), без чисел из stats.
  it("loading показывает skeleton, не значения", () => {
    render(<KpiSection stats={undefined} isLoading={true} isError={false} />);
    expect(screen.queryByText("247")).not.toBeInTheDocument();
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  // Тест: error показывает ErrorState с кнопкой Retry и зовёт onRetry.
  it("error показывает ErrorState с retry", () => {
    const onRetry = vi.fn();
    render(
      <KpiSection
        stats={undefined}
        isLoading={false}
        isError={true}
        error={new Error("boom")}
        onRetry={onRetry}
      />,
    );
    const retry = screen.getByRole("button", { name: /повторить/i });
    expect(retry).toBeInTheDocument();
    retry.click();
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("Dashboard · TaskQueueCard", () => {
  const TASKS: TaskQueueRow[] = [
    {
      id: "t1",
      fb_ad_id: "120211",
      ad_name: "UA17 | SP | MV | Krov | 24.03",
      task_type: "disable",
      status: "RUNNING",
      attempt_count: 1,
      max_attempts: 5,
      requested_by: "bot_auto_stop",
      requested_by_chat_id: null,
      created_at: "2026-05-28T14:30:00Z",
      updated_at: "2026-05-28T14:30:00Z", // required в backend TaskQueueRowOut
      next_attempt_at: null,
      last_error_message: null,
    },
    {
      id: "t2",
      fb_ad_id: "120212",
      ad_name: "DRC | CR2 | MV | Tyver | 25.03",
      task_type: "disable",
      status: "SUCCEEDED",
      attempt_count: 1,
      max_attempts: 5,
      requested_by: "bot_auto_stop",
      requested_by_chat_id: null,
      created_at: "2026-05-28T14:18:00Z",
      updated_at: "2026-05-28T14:19:00Z", // required в backend TaskQueueRowOut
      next_attempt_at: null,
      last_error_message: null,
    },
  ];

  // Тест: рендерит строки задач + считает pending (RUNNING входит, SUCCEEDED нет).
  it("рендерит задачи и счётчик pending", () => {
    render(
      <TaskQueueCard title="Disable queue" tasks={TASKS} isLoading={false} isError={false} />,
    );
    expect(screen.getByText("Disable queue")).toBeInTheDocument();
    expect(screen.getByText("UA17 | SP | MV | Krov | 24.03")).toBeInTheDocument();
    // RUNNING → pending=1; SUCCEEDED не считается pending.
    expect(screen.getByText("1 в очереди")).toBeInTheDocument();
    // Бэкенд SUCCEEDED отображается как "done" (status_mapper-совместимость).
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  // Тест: пустой массив → EmptyState "Очередь пуста".
  it("empty показывает 'Очередь пуста'", () => {
    render(<TaskQueueCard title="Enable queue" tasks={[]} isLoading={false} isError={false} />);
    expect(screen.getByText("Очередь пуста")).toBeInTheDocument();
  });
});

describe("Dashboard · IncidentsCard", () => {
  // Тест: пустой список инцидентов → editorial empty-state "Всё чисто".
  it("empty показывает 'Всё чисто'", () => {
    render(
      <IncidentsCard
        incidents={[]}
        isLoading={false}
        isError={false}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Всё чисто")).toBeInTheDocument();
    expect(screen.getByText("0 открытых")).toBeInTheDocument();
  });

  // Тест: клик по строке инцидента зовёт onSelect с fb_ad_id.
  it("клик по инциденту зовёт onSelect", () => {
    const onSelect = vi.fn();
    const incident: Incident = {
      fb_ad_id: "120999",
      internal_id: "int-1",
      ad_name: "CR2 | DRC | MV | Krov | 25.03",
      campaign_name: null,
      adset_name: null,
      offer_code: "DRC_CR2",
      offer_id: null,
      alert_state: "stop_sent",
      snoozed_until: null,
      open_state_token: null,
      last_warning_at: null,
      last_stop_at: null,
      is_active: true,
      last_seen_at: null,
      delivery_status: null,
      meta_ad_status: null,
      stop_rule_codes: ["CPL_HIGH"],
      warning_rule_codes: [],
      metrics: null,
      incident_open_since: "2026-05-28T14:00:00Z",
      incident_duration_seconds: 600,
      transitions_count: 2,
    };
    render(
      <IncidentsCard
        incidents={[incident]}
        isLoading={false}
        isError={false}
        onSelect={onSelect}
      />,
    );
    screen.getByText("CR2 | DRC | MV | Krov | 25.03").click();
    expect(onSelect).toHaveBeenCalledWith("120999");
  });
});
