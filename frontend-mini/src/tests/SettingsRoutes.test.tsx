/**
 * Полноэкранные роуты /settings/{display,observer,telegram,vision}.
 * Часть 2 issue #342: Sheet с тройным вложенным скроллом заменён на
 * detail-роуты по тому же паттерну, что /desktop и /analytics.
 */
import type { ComponentType } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const roleState = vi.hoisted(() => ({ role: "owner" as string | null }));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

vi.mock("@/lib/auth", () => ({
  getStoredRole: () => roleState.role,
}));

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title, eyebrow }: { title: string; eyebrow?: string }) => (
    <header>
      {eyebrow ? <span>{eyebrow}</span> : null}
      <h1>{title}</h1>
    </header>
  ),
}));

vi.mock("@/features/settings/DisplaySettings", () => ({
  DisplaySettings: ({ canEdit }: { canEdit: boolean }) => (
    <div data-testid="display-settings">canEdit={String(canEdit)}</div>
  ),
}));
vi.mock("@/features/settings/ObserverSettings", () => ({
  ObserverSettings: ({ canEdit }: { canEdit: boolean }) => (
    <div data-testid="observer-settings">canEdit={String(canEdit)}</div>
  ),
}));
vi.mock("@/features/settings/TelegramSettings", () => ({
  TelegramSettings: ({ canEdit }: { canEdit: boolean }) => (
    <div data-testid="telegram-settings">canEdit={String(canEdit)}</div>
  ),
}));
vi.mock("@/features/settings/VisionSettings", () => ({
  VisionSettings: ({ canEdit }: { canEdit: boolean }) => (
    <div data-testid="vision-settings">canEdit={String(canEdit)}</div>
  ),
}));

import { Route as DisplayRoute } from "@/routes/settings/display";
import { Route as ObserverRoute } from "@/routes/settings/observer";
import { Route as TelegramRoute } from "@/routes/settings/telegram";
import { Route as VisionRoute } from "@/routes/settings/vision";

function componentOf(route: unknown) {
  return (route as { component: ComponentType }).component;
}

describe("полноэкранные роуты настроек", () => {
  beforeEach(() => {
    roleState.role = "owner";
  });

  it("/settings/display рендерит заголовок и передаёт canEdit фиче", () => {
    const Page = componentOf(DisplayRoute);
    render(<Page />);
    expect(screen.getByRole("heading", { name: "Отображение" })).toBeInTheDocument();
    expect(screen.getByTestId("display-settings")).toHaveTextContent("canEdit=true");
  });

  it("/settings/observer рендерит заголовок и передаёт canEdit фиче", () => {
    const Page = componentOf(ObserverRoute);
    render(<Page />);
    expect(screen.getByRole("heading", { name: "Observer" })).toBeInTheDocument();
    expect(screen.getByTestId("observer-settings")).toHaveTextContent("canEdit=true");
  });

  it("/settings/telegram рендерит заголовок и передаёт canEdit фиче", () => {
    const Page = componentOf(TelegramRoute);
    render(<Page />);
    expect(screen.getByRole("heading", { name: "Telegram" })).toBeInTheDocument();
    expect(screen.getByTestId("telegram-settings")).toHaveTextContent("canEdit=true");
  });

  it("/settings/vision рендерит заголовок и передаёт canEdit фиче", () => {
    const Page = componentOf(VisionRoute);
    render(<Page />);
    expect(screen.getByRole("heading", { name: "Vision и desktop" })).toBeInTheDocument();
    expect(screen.getByTestId("vision-settings")).toHaveTextContent("canEdit=true");
  });

  it("fail-closed: получатель без роли owner не получает canEdit", () => {
    roleState.role = "recipient";
    const Page = componentOf(ObserverRoute);
    render(<Page />);
    expect(screen.getByTestId("observer-settings")).toHaveTextContent("canEdit=false");
  });
});
