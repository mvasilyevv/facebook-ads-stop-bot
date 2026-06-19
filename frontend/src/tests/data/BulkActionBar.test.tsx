/**
 * Тесты BulkActionBar (канон ads-web.jsx BulkBar) — floating-панель действий.
 * Содержимое: «N выбрано» + Disable + Очистить.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { BulkActionBar } from "@/components/domain/ads/BulkActionBar";

const defaultProps = {
  count: 3,
  onDisable: vi.fn(),
  onClear: vi.fn(),
};

describe("BulkActionBar", () => {
  // Счётчик выбранных.
  it("показывает количество выбранных строк", () => {
    render(<BulkActionBar {...defaultProps} count={5} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/выбрано/)).toBeInTheDocument();
  });

  // Disable → onDisable.
  it("кнопка Disable вызывает onDisable", async () => {
    const user = userEvent.setup();
    const onDisable = vi.fn();
    render(<BulkActionBar {...defaultProps} onDisable={onDisable} />);
    await user.click(screen.getByRole("button", { name: /Отключить.*объявлений/i }));
    expect(onDisable).toHaveBeenCalledTimes(1);
  });

  // Очистить → onClear.
  it("кнопка Очистить вызывает onClear", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(<BulkActionBar {...defaultProps} onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /Очистить выбор/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  // isPending блокирует все кнопки.
  it("isPending=true блокирует все кнопки", () => {
    render(<BulkActionBar {...defaultProps} isPending />);
    expect(screen.getByRole("button", { name: /Отключить.*объявлений/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Очистить/i })).toBeDisabled();
  });

  // toolbar aria-label содержит count.
  it("toolbar имеет aria-label с количеством", () => {
    render(<BulkActionBar {...defaultProps} count={7} />);
    expect(screen.getByRole("toolbar")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("7"),
    );
  });
});
