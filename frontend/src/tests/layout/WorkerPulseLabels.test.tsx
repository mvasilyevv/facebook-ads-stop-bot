/**
 * Тесты на локализацию статусов и сортировку воркеров в WorkerPulse.
 *
 * Тест на порядок был написан до правки и на исходном коде падал с:
 *   AssertionError: expected "В работе" not to equal "В работе"
 * Причина: сортировка сравнивала с ONLINE (uppercase), сервер отдаёт "online" —
 * условие никогда не срабатывало, первый span оставался «В работе» (online-воркер).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockRealtimeStatus = vi.fn(() => "connected");

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

import { WorkerPulse } from "@/components/layout/WorkerPulse";

describe("WorkerPulse — локализация статусов", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("показывает русские подписи для статусов из фикстуры (online, degraded, stalled)", async () => {
    render(<WorkerPulse />);
    screen.getByRole("button", { name: /Воркеры/ }).focus();
    await userEvent.keyboard("{Enter}");

    // Ожидаем русские подписи
    expect(screen.getAllByText("В работе").length).toBeGreaterThan(0);
    expect(screen.getByText("С ограничениями")).toBeInTheDocument();
    expect(screen.getByText("Не разбирает очередь")).toBeInTheDocument();

    // Сырые английские строки не должны присутствовать
    expect(screen.queryByText("online")).not.toBeInTheDocument();
    expect(screen.queryByText("running")).not.toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
    expect(screen.queryByText("failed")).not.toBeInTheDocument();
    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
    expect(screen.queryByText("degraded")).not.toBeInTheDocument();
  });
});

describe("WorkerPulse — сортировка упавших вперёд", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("воркер со статусом stalled (последний в массиве) рендерится первой строкой попапа", async () => {
    // В фикстуре: workers = [online, degraded, online, stalled].
    // После сортировки «не-online вперёд» stalled и degraded идут первыми.
    // На старом коде (сравнение с ONLINE uppercase) порядок не менялся —
    // stalled оставался последним, первый статус-span был «В работе»,
    // и тест падал: expect(statusSpans[0]?.textContent).not.toBe("В работе")
    //   → received "В работе" (AssertionError)
    render(<WorkerPulse />);
    screen.getByRole("button", { name: /Воркеры/ }).focus();
    await userEvent.keyboard("{Enter}");

    // Первый статусный span в попапе должен принадлежать не-online воркеру.
    const statusSpans = screen
      .getAllByText(/В работе|С ограничениями|Не разбирает очередь/)
      .filter((el) => el.tagName === "SPAN");

    // Первый элемент в DOM-порядке — не «В работе», а упавший воркер.
    expect(statusSpans[0]?.textContent).not.toBe("В работе");
  });
});
