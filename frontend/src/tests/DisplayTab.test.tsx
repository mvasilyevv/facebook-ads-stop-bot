import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  update: vi.fn(),
  query: {
    data: {
      timezone_name: "Europe/Kaliningrad",
      updated_at: "2026-08-09T10:00:00Z",
    } as { timezone_name: string; updated_at: string } | undefined,
    isPending: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
  },
}));

vi.mock("@/lib/api/settings", () => ({
  useOperatorDisplayPreference: () => hooks.query,
  useUpdateOperatorDisplayPreference: () => ({
    mutate: hooks.update,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("@/lib/timezone", () => ({
  browserTimeZone: () => "Asia/Bangkok",
  formatDisplayDateTime: () => "09 авг. 2026 г., 12:00",
}));

import { DisplayTab } from "@/components/settings/DisplayTab";

describe("DisplayTab server preference", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.query.data = {
      timezone_name: "Europe/Kaliningrad",
      updated_at: "2026-08-09T10:00:00Z",
    };
    hooks.query.isPending = false;
    hooks.query.isError = false;
    hooks.query.error = null;
    globalThis.localStorage.clear();
  });

  it("writes the IANA timezone to the shared server preference", () => {
    render(<DisplayTab />);

    expect(screen.getByText(/Сохранённый часовой пояс: Europe\/Kaliningrad/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Часовой пояс"), {
      target: { value: "Europe/London" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(hooks.update).toHaveBeenCalledWith(
      { body: { timezone_name: "Europe/London" } },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(globalThis.localStorage.length).toBe(0);
  });

  it("uses the device timezone only after an explicit owner action", () => {
    render(<DisplayTab />);

    expect(hooks.update).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Использовать Asia/Bangkok" }));
    expect(hooks.update).toHaveBeenCalledWith(
      { body: { timezone_name: "Asia/Bangkok" } },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("leaves semantic IANA validation to the backend authority", () => {
    render(<DisplayTab />);

    fireEvent.change(screen.getByLabelText("Часовой пояс"), {
      target: { value: "Mars/Olympus" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(hooks.update).toHaveBeenCalledWith(
      { body: { timezone_name: "Mars/Olympus" } },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("fails closed without a confirmed server preference", () => {
    hooks.query.data = undefined;
    hooks.query.isError = true;
    hooks.query.error = new Error(
      "traceback postgres://secret 00000000-0000-0000-0000-000000000099",
    );

    render(<DisplayTab />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Не удалось загрузить часовой пояс отображения",
    );
    expect(screen.queryByText(/traceback|postgres|00000000-/i)).not.toBeInTheDocument();
  });
});
