import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const updateVision = vi.fn().mockResolvedValue({});

vi.mock("@/lib/api/settings", () => ({
  useVisionSettings: () => ({
    data: {
      has_token: true,
      has_cloud_username: false,
      has_cloud_password: false,
      has_team_id: false,
      has_folder_id: false,
      profile_id: "profile-1",
      channel_status: "READY",
      channel_message: "Канал Vision жив.",
      channel_next_step: "Действий не требуется.",
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useVisionProfiles: () => ({
    data: {
      state: "ready",
      reason: "READY",
      message: "",
      items: [{ id: "profile-1", name: "Desk 10", status: null, tags: [], running: false }],
      selected_profile_id: "profile-1",
      selected_present: true,
    },
    isPending: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useUpdateVisionSettings: () => ({ mutateAsync: updateVision, isPending: false }),
  useReconnectVision: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/components/ui/Toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { VisionTab } from "@/components/settings/VisionTab";

describe("VisionTab cloud credentials", () => {
  it("sends all non-empty cloud fields and keeps password input non-autofilled", async () => {
    const user = userEvent.setup();
    render(<VisionTab />);

    await user.type(screen.getByLabelText("Логин"), "vision-user");
    await user.type(screen.getByLabelText("Пароль"), "vision-password");
    await user.type(screen.getByLabelText("Team ID"), "team-1");
    await user.type(screen.getByLabelText("Folder ID"), "folder-1");

    const password = screen.getByLabelText("Пароль");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "new-password");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(updateVision).toHaveBeenCalledWith({
      profile_id: "profile-1",
      username: "vision-user",
      password: "vision-password",
      team_id: "team-1",
      folder_id: "folder-1",
    });
    expect(password).toHaveValue("");
  });
});
