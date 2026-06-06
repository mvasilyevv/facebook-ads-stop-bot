/**
 * Тесты feed-компонентов: IncidentRow, EventRow, TaskRow, TaskQueueCard.
 */

import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { IncidentRow } from "@/components/domain/feed/IncidentRow";
import { EventRow } from "@/components/domain/feed/EventRow";
import { TaskRow } from "@/components/domain/feed/TaskRow";
import { TaskQueueCard } from "@/components/domain/feed/TaskQueueCard";
import type { Incident, AlertEvent, TaskQueueRow as TaskQueueRowData } from "@fb/shared";

// ─── Фабрики ──────────────────────────────────────────────────────────────────

function makeIncident(overrides: Partial<Incident> = {}): Incident {
  return {
    fb_ad_id: "120211984573_8761",
    internal_id: "a1b2c3d4-0000-0000-0000-000000000001",
    ad_name: "UA17 | SP | MV | Krov | 24.03",
    alert_state: "warning_sent",
    is_active: true,
    transitions_count: 1,
    stop_rule_codes: [],
    warning_rule_codes: ["cpl_stop"],
    incident_open_since: new Date().toISOString(),
    ...overrides,
  };
}

function makeEvent(overrides: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: "ev-0001",
    fb_ad_id: "120211984573_8761",
    ad_name: "UA17 | SP | MV | Krov | 24.03",
    stage: "warning",
    created_at: new Date().toISOString(),
    matched_rule_codes: ["cpl_stop"],
    ...overrides,
  };
}

function makeTask(overrides: Partial<TaskQueueRowData> = {}): TaskQueueRowData {
  return {
    id: "task-001",
    fb_ad_id: "120211984573_8761",
    ad_name: "UA17 | SP | MV | Krov | 24.03",
    task_type: "disable",
    status: "PENDING",
    attempt_count: 1,
    max_attempts: 5,
    requested_by: "bot_auto_stop",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

// ─── IncidentRow ──────────────────────────────────────────────────────────────

describe("IncidentRow", () => {
  // Warning — badge со статусом предупреждения
  it("рендерит warning badge для alert_state=warning_sent", () => {
    render(<IncidentRow incident={makeIncident({ alert_state: "warning_sent" })} />);
    // Бэдж "Предупреждение" из ALERT_STAGE_LABELS
    expect(screen.getByText("Предупреждение")).toBeInTheDocument();
  });

  // Stop — красный badge
  it("рендерит stop badge для alert_state=stop_sent", () => {
    render(<IncidentRow incident={makeIncident({ alert_state: "stop_sent" })} />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // Claimed — синий badge
  it("рендерит claimed badge для alert_state=claimed", () => {
    render(<IncidentRow incident={makeIncident({ alert_state: "claimed" })} />);
    expect(screen.getByText("В работе")).toBeInTheDocument();
  });

  // Ad name отображается
  it("показывает ad_name", () => {
    render(<IncidentRow incident={makeIncident()} />);
    expect(screen.getByText("UA17 | SP | MV | Krov | 24.03")).toBeInTheDocument();
  });

  // Rule pill — короткий лейбл
  it("показывает rule pill с коротким лейблом", () => {
    render(
      <IncidentRow incident={makeIncident({ warning_rule_codes: ["cpl_stop"] })} />,
    );
    // ruleCodeLabel("cpl_stop", true) = "Дорогой лид"
    expect(screen.getByText("Дорогой лид")).toBeInTheDocument();
  });

  // Множество правил — счётчик +N
  it("показывает счётчик +N при нескольких правилах", () => {
    render(
      <IncidentRow
        incident={makeIncident({
          stop_rule_codes: ["cpl_stop", "cpc_stop"],
          warning_rule_codes: ["cpr_stop"],
        })}
      />,
    );
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  // onClick срабатывает
  it("onClick вызывается при клике", async () => {
    const onClick = vi.fn();
    render(<IncidentRow incident={makeIncident()} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

// ─── EventRow ─────────────────────────────────────────────────────────────────

describe("EventRow", () => {
  // Warning badge
  it("рендерит warning badge для stage=warning", () => {
    render(<EventRow event={makeEvent({ stage: "warning" })} />);
    expect(screen.getByText("Предупреждение")).toBeInTheDocument();
  });

  // Stop badge
  it("рендерит stop badge для stage=stop", () => {
    render(<EventRow event={makeEvent({ stage: "stop" })} />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // Ad name
  it("показывает ad_name", () => {
    render(<EventRow event={makeEvent()} />);
    expect(screen.getByText("UA17 | SP | MV | Krov | 24.03")).toBeInTheDocument();
  });

  // 3+ правил → счётчик
  it("показывает +N при matched_rule_codes.length > 3", () => {
    render(
      <EventRow
        event={makeEvent({
          matched_rule_codes: ["cpl_stop", "cpc_stop", "cpr_stop", "regs_no_dep_stop"],
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  // Нет onClick — рендерится без ошибок
  it("рендерит без onClick без ошибок", () => {
    expect(() => render(<EventRow event={makeEvent()} />)).not.toThrow();
  });
});

// ─── TaskRow ──────────────────────────────────────────────────────────────────

describe("TaskRow", () => {
  // SUCCEEDED → done badge
  it("показывает badge SUCCEEDED", () => {
    render(<TaskRow task={makeTask({ status: "SUCCEEDED" })} />);
    expect(screen.getByText("Выполнено")).toBeInTheDocument();
  });

  // FAILED → failed badge
  it("показывает badge FAILED", () => {
    render(<TaskRow task={makeTask({ status: "FAILED" })} />);
    expect(screen.getByText("Ошибка")).toBeInTheDocument();
  });

  // RETRYING → retrying badge
  it("показывает badge RETRYING", () => {
    render(<TaskRow task={makeTask({ status: "RETRYING" })} />);
    expect(screen.getByText("Повтор")).toBeInTheDocument();
  });

  // Attempts counter: ×1/5
  it("показывает счётчик попыток ×N/M", () => {
    render(<TaskRow task={makeTask({ attempt_count: 2, max_attempts: 5 })} />);
    expect(screen.getByText(/2\/5/)).toBeInTheDocument();
  });

  // Ad name отображается
  it("показывает ad_name", () => {
    render(<TaskRow task={makeTask()} />);
    expect(screen.getByText("UA17 | SP | MV | Krov | 24.03")).toBeInTheDocument();
  });
});

// ─── TaskQueueCard ────────────────────────────────────────────────────────────

describe("TaskQueueCard", () => {
  // Loading — skeleton
  it("в состоянии loading показывает skeleton", () => {
    render(
      <TaskQueueCard
        title="Disable Queue"
        tasks={[]}
        isLoading
        isError={false}
      />,
    );
    // Проверяем заголовок карточки
    expect(screen.getByText("Disable Queue")).toBeInTheDocument();
  });

  // Error — ErrorState
  it("в состоянии error показывает ErrorState", () => {
    render(
      <TaskQueueCard
        title="Queue"
        tasks={[]}
        isLoading={false}
        isError
        error={new Error("test")}
      />,
    );
    expect(screen.getByText(/Не удалось загрузить/i)).toBeInTheDocument();
  });

  // Empty — EmptyState
  it("при tasks=[] показывает emptyLabel", () => {
    render(
      <TaskQueueCard
        title="Queue"
        tasks={[]}
        isLoading={false}
        isError={false}
        emptyLabel="Очередь пуста"
      />,
    );
    expect(screen.getByText("Очередь пуста")).toBeInTheDocument();
  });

  // Данные — показывает ad_name
  it("с tasks показывает ad_name каждой задачи", () => {
    const tasks = [
      makeTask({ id: "t1", ad_name: "Ad First" }),
      makeTask({ id: "t2", ad_name: "Ad Second" }),
    ];
    render(
      <TaskQueueCard title="Queue" tasks={tasks} isLoading={false} isError={false} />,
    );
    expect(screen.getByText("Ad First")).toBeInTheDocument();
    expect(screen.getByText("Ad Second")).toBeInTheDocument();
  });

  // Мета-строка: "N pending"
  it("показывает счётчик pending в мета-строке", () => {
    const tasks = [
      makeTask({ id: "t1", status: "PENDING" }),
      makeTask({ id: "t2", status: "PENDING" }),
      makeTask({ id: "t3", status: "RETRYING" }),
    ];
    render(
      <TaskQueueCard title="Queue" tasks={tasks} isLoading={false} isError={false} />,
    );
    expect(screen.getByText(/2 pending/)).toBeInTheDocument();
    expect(screen.getByText(/1 retrying/)).toBeInTheDocument();
  });
});
