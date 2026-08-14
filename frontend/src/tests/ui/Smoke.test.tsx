/**
 * Smoke-тесты — рендер без ошибок для остальных UI-компонентов.
 * По одному тесту на компонент. Проверяем только что рендер не крашится.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Kbd } from "@/components/ui/Kbd";
import { Pill, FilterPill, Chip } from "@/components/ui/Pill";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Card } from "@/components/ui/Card";
import { Spinner, ProgressBar } from "@/components/ui/Spinner";

describe("Kbd", () => {
  it("рендерится", () => {
    render(<Kbd>⌘</Kbd>);
    expect(screen.getByText("⌘")).toBeInTheDocument();
  });
});

describe("Pill", () => {
  it("Pill рендерится", () => {
    render(<Pill>CPL_HIGH</Pill>);
    expect(screen.getByText("CPL_HIGH")).toBeInTheDocument();
  });

  it("FilterPill рендерится", () => {
    render(<FilterPill>STOP</FilterPill>);
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("FilterPill активный имеет aria-pressed", () => {
    render(<FilterPill active>WARN</FilterPill>);
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });

  it("Chip рендерится с кнопкой удаления", () => {
    render(<Chip onRemove={() => {}}>offer = DRC</Chip>);
    expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument();
  });
});

describe("Input", () => {
  it("рендерится", () => {
    render(<Input placeholder="Поиск" />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("error message рендерится с role=alert", () => {
    render(<Input errorMessage="Обязательное поле" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("Select", () => {
  it("рендерится с опциями", () => {
    render(
      <Select
        options={[
          { value: "a", label: "Alpha" },
          { value: "b", label: "Beta" },
        ]}
      />,
    );
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });
});

describe("Switch", () => {
  it("рендерится с aria-checked", () => {
    render(<Switch checked={false} onChange={() => {}} label="Toggle" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("checked=true → aria-checked true", () => {
    render(<Switch checked={true} onChange={() => {}} label="Toggle" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });
});

describe("Skeleton", () => {
  it("рендерится с role=status", () => {
    render(<Skeleton />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

});

describe("EmptyState", () => {
  it("рендерит title и description", () => {
    render(<EmptyState title="Пусто" description="Нет данных" />);
    expect(screen.getByText("Пусто")).toBeInTheDocument();
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("рендерится с role=alert", () => {
    render(<ErrorState error="Test error" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("message обрезается до 400 символов", () => {
    const long = "A".repeat(500);
    render(<ErrorState error={long} />);
    // Рендерится без ошибки
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("не печатает сырой exception и traceback", () => {
    render(<ErrorState error={new Error("Traceback: postgres://user:pw@db-01")} />);
    const alert = screen.getByRole("alert");
    expect(alert).not.toHaveTextContent("Traceback");
    expect(alert).not.toHaveTextContent("postgres");
    expect(alert).toHaveTextContent("Подробности недоступны. Повторите попытку.");
  });

  it("не печатает JSON-дамп произвольного payload и correlation_id", () => {
    render(
      <ErrorState
        error={{
          detail: "internal worker crash",
          correlation_id: "00000000-0000-0000-0000-000000000099",
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).not.toHaveTextContent("internal worker crash");
    expect(alert).not.toHaveTextContent("00000000-0000-0000-0000-000000000099");
    expect(alert).not.toHaveTextContent("{");
  });

  it("показывает message канонического ApiProblem без его correlation_id", () => {
    render(
      <ErrorState
        error={{
          code: "snapshot_unavailable",
          message: "Снимок временно недоступен",
          correlation_id: "00000000-0000-0000-0000-000000000099",
          field_errors: null,
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Снимок временно недоступен");
    expect(alert).not.toHaveTextContent("00000000-0000-0000-0000-000000000099");
  });
});

describe("Card", () => {
  it("рендерится с children", () => {
    render(<Card>Контент</Card>);
    expect(screen.getByText("Контент")).toBeInTheDocument();
  });

  it("рендерится с eyebrow и title", () => {
    render(
      <Card eyebrow="01 / OVERVIEW" title="Активные">
        body
      </Card>,
    );
    expect(screen.getByText("Активные")).toBeInTheDocument();
    expect(screen.getByText("01 / OVERVIEW")).toBeInTheDocument();
  });
});

describe("Spinner", () => {
  it("рендерится с role=status", () => {
    render(<Spinner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("ProgressBar", () => {
  it("определённое значение", () => {
    render(<ProgressBar value={50} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
  });

  it("indeterminate — нет aria-valuenow", () => {
    render(<ProgressBar />);
    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
  });
});
