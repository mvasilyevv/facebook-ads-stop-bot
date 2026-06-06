/**
 * Тесты Timeline — сортировка по времени (DESC), типы dots, rule-pills.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Timeline } from "@/components/data/timeline/Timeline";
import type { TimelineItem } from "@/components/data/timeline/Timeline";

const ITEMS: TimelineItem[] = [
  {
    id: "1",
    ts: "2026-06-06T14:32:18Z",
    type: "stop",
    title: "STOP triggered",
    ruleCodes: ["cpl_stop", "spend_no_dep_range"],
    meta: "Open token f3a8c…921",
  },
  {
    id: "2",
    ts: "2026-06-06T14:32:20Z",
    type: "task",
    title: "Disable task enqueued",
    meta: "requested by bot_auto_stop · attempt 1/5",
  },
  {
    id: "3",
    ts: "2026-06-06T14:18:02Z",
    type: "warning",
    title: "WARNING triggered",
    ruleCodes: ["cpl_stop"],
  },
  {
    id: "4",
    ts: "2026-06-06T10:00:00Z",
    type: "default",
    title: "Cabinet day started",
    meta: "Vision session reopened",
  },
];

describe("Timeline", () => {
  // Рендерит все события
  it("рендерит все заголовки событий", () => {
    render(<Timeline items={ITEMS} />);
    expect(screen.getByText("STOP triggered")).toBeInTheDocument();
    expect(screen.getByText("Disable task enqueued")).toBeInTheDocument();
    expect(screen.getByText("WARNING triggered")).toBeInTheDocument();
    expect(screen.getByText("Cabinet day started")).toBeInTheDocument();
  });

  // Сортировка DESC: task (14:32:20) должна быть выше stop (14:32:18)
  it("сортировка DESC — новейшие сверху", () => {
    render(<Timeline items={ITEMS} />);
    const titles = screen.getAllByText(/triggered|enqueued|started/).map((el) => el.textContent);
    // "Disable task enqueued" (14:32:20) должен идти до "STOP triggered" (14:32:18)
    const taskIdx = titles.findIndex((t) => t?.includes("Disable"));
    const stopIdx = titles.findIndex((t) => t?.includes("STOP"));
    expect(taskIdx).toBeLessThan(stopIdx);
  });

  // Rule pills рендерятся
  it("рендерит rule pills из ruleCodes", () => {
    render(<Timeline items={ITEMS} />);
    // ruleCodeLabel("cpl_stop", short=true) → "Дорогой лид"
    const pills = screen.getAllByText("Дорогой лид");
    expect(pills.length).toBeGreaterThan(0);
  });

  // Типы dots через data-timeline-type атрибут
  it("stop-событие имеет data-timeline-type=stop", () => {
    const item = ITEMS.find((i) => i.type === "stop")!;
    render(<Timeline items={[item]} />);
    const row = document.querySelector('[data-timeline-type="stop"]');
    expect(row).toBeInTheDocument();
  });

  it("task-событие имеет data-timeline-type=task", () => {
    const item = ITEMS.find((i) => i.type === "task")!;
    render(<Timeline items={[item]} />);
    const row = document.querySelector('[data-timeline-type="task"]');
    expect(row).toBeInTheDocument();
  });

  it("warning-событие имеет data-timeline-type=warning", () => {
    const item = ITEMS.find((i) => i.type === "warning")!;
    render(<Timeline items={[item]} />);
    const row = document.querySelector('[data-timeline-type="warning"]');
    expect(row).toBeInTheDocument();
  });

  it("default-событие имеет data-timeline-type=default", () => {
    const item = ITEMS.find((i) => i.type === "default")!;
    render(<Timeline items={[item]} />);
    const row = document.querySelector('[data-timeline-type="default"]');
    expect(row).toBeInTheDocument();
  });

  // Пустой список → empty-message
  it("пустой список — показывает сообщение", () => {
    render(<Timeline items={[]} emptyMessage="Нет данных" />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // Пустой список без emptyMessage — показывает дефолт
  it("пустой список — дефолтное сообщение", () => {
    render(<Timeline items={[]} />);
    expect(screen.getByText("Событий нет")).toBeInTheDocument();
  });

  // Meta-строка рендерится
  it("мета-строка рендерится для task-события", () => {
    const taskItem = ITEMS.find((i) => i.type === "task")!;
    render(<Timeline items={[taskItem]} />);
    expect(screen.getByText("requested by bot_auto_stop · attempt 1/5")).toBeInTheDocument();
  });

  // role=list aria-label
  it("контейнер имеет role=list", () => {
    render(<Timeline items={ITEMS} />);
    expect(screen.getByRole("list", { name: "Таймлайн событий" })).toBeInTheDocument();
  });
});
