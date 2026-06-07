/**
 * Тесты BulkActionBar — кнопки, колбэки, snooze dropdown.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { BulkActionBar } from "@/components/domain/ads/BulkActionBar";

// ─── Базовые пропсы ────────────────────────────────────────────────────────

const defaultProps = {
  count: 3,
  onDisable: vi.fn(),
  onSnooze: vi.fn(),
  onMarkClaimed: vi.fn(),
  onClear: vi.fn(),
};

describe("BulkActionBar", () => {
  // Счётчик выбранных отображается
  it("показывает количество выбранных строк", () => {
    render(<BulkActionBar {...defaultProps} count={5} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/выбрано/)).toBeInTheDocument();
  });

  // onDisable вызывается по кнопке Отключить
  it("кнопка Отключить вызывает onDisable", async () => {
    const user = userEvent.setup();
    const onDisable = vi.fn();
    render(<BulkActionBar {...defaultProps} onDisable={onDisable} />);

    await user.click(screen.getByRole("button", { name: /Отключить.*объявлений/i }));
    expect(onDisable).toHaveBeenCalledTimes(1);
  });

  // onMarkClaimed вызывается по кнопке В работе
  it("кнопка «В работе» вызывает onMarkClaimed", async () => {
    const user = userEvent.setup();
    const onMarkClaimed = vi.fn();
    render(<BulkActionBar {...defaultProps} onMarkClaimed={onMarkClaimed} />);

    await user.click(screen.getByRole("button", { name: /Отметить.*объявлений.*в работе/i }));
    expect(onMarkClaimed).toHaveBeenCalledTimes(1);
  });

  // onClear вызывается по кнопке Сбросить
  it("кнопка Сбросить вызывает onClear", async () => {
    const user = userEvent.setup();
    const onClear = vi.fn();
    render(<BulkActionBar {...defaultProps} onClear={onClear} />);

    await user.click(screen.getByRole("button", { name: /Сбросить выбор/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  // Snooze dropdown раскрывается при клике на кнопку
  it("клик на Снуз открывает dropdown с вариантами", async () => {
    const user = userEvent.setup();
    render(<BulkActionBar {...defaultProps} />);

    const snoozeBtn = screen.getByRole("button", { name: /Снузировать/i });
    await user.click(snoozeBtn);

    // Варианты должны появиться
    expect(screen.getByText("15 минут")).toBeInTheDocument();
    expect(screen.getByText("1 час")).toBeInTheDocument();
    expect(screen.getByText("4 часа")).toBeInTheDocument();
  });

  // Выбор варианта снуза вызывает onSnooze с правильными минутами
  it("выбор 30 минут вызывает onSnooze(30)", async () => {
    const user = userEvent.setup();
    const onSnooze = vi.fn();
    render(<BulkActionBar {...defaultProps} onSnooze={onSnooze} />);

    // Открыть dropdown
    await user.click(screen.getByRole("button", { name: /Снузировать/i }));
    // Выбрать 30 минут
    await user.click(screen.getByText("30 минут"));

    expect(onSnooze).toHaveBeenCalledWith(30);
  });

  // Выбор варианта снуза закрывает dropdown
  it("после выбора снуза dropdown закрывается", async () => {
    const user = userEvent.setup();
    render(<BulkActionBar {...defaultProps} />);

    await user.click(screen.getByRole("button", { name: /Снузировать/i }));
    await user.click(screen.getByText("1 час"));

    expect(screen.queryByText("15 минут")).not.toBeInTheDocument();
  });

  // При isPending все кнопки disabled
  it("isPending=true блокирует все кнопки", () => {
    render(<BulkActionBar {...defaultProps} isPending={true} />);

    expect(screen.getByRole("button", { name: /Отключить.*объявлений/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Снузировать/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Отметить.*объявлений/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Сбросить выбор/i })).toBeDisabled();
  });

  // aria-label содержит count
  it("toolbar имеет aria-label с количеством", () => {
    render(<BulkActionBar {...defaultProps} count={7} />);
    const toolbar = screen.getByRole("toolbar");
    expect(toolbar).toHaveAttribute("aria-label", expect.stringContaining("7"));
  });
});
