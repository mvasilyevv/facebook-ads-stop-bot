/**
 * ScanningControl — выключение сканирования снимает защитный контур с кабинетов
 * и потому требует подтверждения; включение возвращает контур и остаётся одним кликом.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScanningControl, nextScanEtaLabel } from "./ScanningControl";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";

const mockObserverSettings = vi.hoisted(() => vi.fn());
const mockToggleScanning = vi.hoisted(() => vi.fn());
const mockToast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: () => mockObserverSettings(),
  useToggleScanning: () => ({ mutateAsync: mockToggleScanning, isPending: false }),
}));

vi.mock("@/components/ui/toastStore", () => ({
  toast: mockToast,
}));

function readySystem(): OperatorSnapshot["system"] {
  return {
    state: "ready",
    data: { next_scan_at: null },
  } as OperatorSnapshot["system"];
}

describe("ScanningControl", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("не выключает сканирование без подтверждения", async () => {
    const user = userEvent.setup();
    mockObserverSettings.mockReturnValue({ data: { is_scanning_enabled: true }, isPending: false });
    mockToggleScanning.mockResolvedValue({});
    render(<ScanningControl system={readySystem()} />);

    await user.click(
      screen.getByRole("switch", { name: "Остановить периодическое сканирование" }),
    );

    expect(mockToggleScanning).not.toHaveBeenCalled();
    expect(
      screen.getByText("Авто-стоп перестанет следить за кабинетами до включения."),
    ).toBeInTheDocument();
  });

  it("подтверждение диалога выключает сканирование", async () => {
    const user = userEvent.setup();
    mockObserverSettings.mockReturnValue({ data: { is_scanning_enabled: true }, isPending: false });
    mockToggleScanning.mockResolvedValue({});
    render(<ScanningControl system={readySystem()} />);

    await user.click(
      screen.getByRole("switch", { name: "Остановить периодическое сканирование" }),
    );
    await user.click(screen.getByRole("button", { name: "Выключить сканирование" }));

    expect(mockToggleScanning).toHaveBeenCalledWith(false);
    expect(mockToast.success).toHaveBeenCalledWith("Сканирование остановлено");
  });

  it("отмена диалога не трогает состояние сканирования", async () => {
    const user = userEvent.setup();
    mockObserverSettings.mockReturnValue({ data: { is_scanning_enabled: true }, isPending: false });
    render(<ScanningControl system={readySystem()} />);

    await user.click(
      screen.getByRole("switch", { name: "Остановить периодическое сканирование" }),
    );
    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(mockToggleScanning).not.toHaveBeenCalled();
    expect(
      screen.queryByText("Авто-стоп перестанет следить за кабинетами до включения."),
    ).not.toBeInTheDocument();
  });

  it("включает сканирование сразу, без подтверждения", async () => {
    const user = userEvent.setup();
    mockObserverSettings.mockReturnValue({ data: { is_scanning_enabled: false }, isPending: false });
    mockToggleScanning.mockResolvedValue({});
    const { container } = render(<ScanningControl system={readySystem()} />);

    await user.click(
      screen.getByRole("switch", { name: "Включить периодическое сканирование" }),
    );

    expect(mockToggleScanning).toHaveBeenCalledWith(true);
    expect(within(container).queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("nextScanEtaLabel", () => {
  it("возвращает null без next_scan_at", () => {
    expect(nextScanEtaLabel(null, Date.now())).toBeNull();
  });

  it("округляет остаток до минут", () => {
    const now = Date.now();
    expect(nextScanEtaLabel(new Date(now + 5 * 60_000).toISOString(), now)).toBe(
      "цикл через 5 мин",
    );
  });
});
