/**
 * Тесты ConfirmDialog — confirm/cancel, typed confirmation, loading state.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  title: "Удалить кампанию?",
  description: "Это необратимо.",
  onConfirm: vi.fn(),
};

describe("ConfirmDialog", () => {
  // Title и description видны
  it("рендерит title и description", () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByText("Удалить кампанию?")).toBeInTheDocument();
    expect(screen.getByText("Это необратимо.")).toBeInTheDocument();
  });

  // Без confirmWord — кнопка сразу активна
  it("без confirmWord — confirm кнопка активна", () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByRole("button", { name: "Подтвердить" })).not.toBeDisabled();
  });

  // Клик Cancel вызывает onOpenChange(false)
  it("Cancel вызывает onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} onOpenChange={onOpenChange} />);
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // Клик Confirm вызывает onConfirm
  it("Confirm клик вызывает onConfirm", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} onConfirm={onConfirm} onOpenChange={onOpenChange} />);
    await user.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  // С confirmWord — кнопка заблокирована пока не введено слово
  it("confirmWord — кнопка disabled пока не введено слово", () => {
    render(<ConfirmDialog {...defaultProps} confirmWord="УДАЛИТЬ" />);
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
  });

  // С confirmWord — кнопка активируется после ввода правильного слова
  it("confirmWord — кнопка активируется при правильном вводе", async () => {
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} confirmWord="УДАЛИТЬ" />);
    const input = screen.getByRole("textbox");
    await user.type(input, "УДАЛИТЬ");
    expect(screen.getByRole("button", { name: "Подтвердить" })).not.toBeDisabled();
  });

  // Неправильный ввод — кнопка disabled
  it("confirmWord — неправильный ввод → кнопка disabled", async () => {
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} confirmWord="УДАЛИТЬ" />);
    await user.type(screen.getByRole("textbox"), "удалить");
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
  });

  // Danger variant — кнопка рендерится
  it("confirmVariant danger рендерится", () => {
    render(<ConfirmDialog {...defaultProps} confirmVariant="danger" confirmLabel="Удалить" />);
    expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument();
  });

  // Primary variant — кнопка рендерится
  it("confirmVariant primary рендерится", () => {
    render(<ConfirmDialog {...defaultProps} confirmVariant="primary" confirmLabel="Одобрить" />);
    expect(screen.getByRole("button", { name: "Одобрить" })).toBeInTheDocument();
  });

  // Esc закрывает
  it("Esc вызывает onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} onOpenChange={onOpenChange} />);
    await user.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
