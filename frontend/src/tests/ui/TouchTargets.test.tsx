import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Modal } from "@/components/ui/Modal";
import { ToastViewport, toast } from "@/components/ui/Toast";
import { useToastStore } from "@/components/ui/toastStore";

afterEach(() => {
  useToastStore.setState({ toasts: [] });
});

describe("critical UI touch targets", () => {
  it("keeps the modal close control at least 44px", () => {
    render(
      <Modal open onOpenChange={() => undefined} title="Проверка">
        Контент
      </Modal>,
    );

    expect(screen.getByRole("button", { name: "Закрыть" })).toHaveClass("size-11");
  });

  it("keeps the persistent toast close control at least 44px", () => {
    toast.error("Ошибка");
    render(<ToastViewport />);

    expect(screen.getByRole("button", { name: "Закрыть" })).toHaveClass("size-11");
  });
});
