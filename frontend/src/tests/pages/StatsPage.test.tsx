import { describe, expect, it, vi } from "vitest";

const { redirectError, redirectMock } = vi.hoisted(() => {
  const error = new Error("redirect");
  return { redirectError: error, redirectMock: vi.fn(() => error) };
});

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (options: { beforeLoad: () => unknown }) => options,
  redirect: redirectMock,
}));

import { Route } from "@/routes/stats/index";

describe("legacy /stats route", () => {
  it("redirects to the uploads tab of unified analytics", () => {
    const beforeLoad = (Route as unknown as { beforeLoad: () => unknown }).beforeLoad;
    expect(() => beforeLoad()).toThrow(redirectError);
    expect(redirectMock).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "/analytics",
        replace: true,
        search: expect.objectContaining({ tab: "uploads", period: "today" }),
      }),
    );
  });
});
