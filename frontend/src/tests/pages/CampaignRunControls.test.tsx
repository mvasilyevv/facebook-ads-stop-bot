import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  abort: vi.fn(),
  detail: null as Record<string, unknown> | null,
  detailRefetch: vi.fn(),
  listRefetch: vi.fn(),
  resume: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  toastSuccess: vi.fn(),
  toastWarning: vi.fn(),
}));

vi.mock("@/lib/api/campaigns", () => ({
  RUN_STATUS_LABELS: {
    queued: "В очереди",
    uniquifying: "Уникализация",
    uploading: "Загрузка",
    creating: "Создание",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменено",
  },
  useAbortCampaignRun: () => ({
    isPending: false,
    mutateAsync: mocks.abort,
  }),
  useResumeCampaignRun: () => ({
    isPending: false,
    mutateAsync: mocks.resume,
  }),
  useRunDetail: () => ({
    data: mocks.detail,
    error: null,
    isError: false,
    isLoading: false,
    refetch: mocks.detailRefetch,
  }),
  useRuns: () => ({
    data: {
      data: [
        {
          id: "11111111-2222-3333-4444-555555555555",
          preset_id: null,
          status: "creating",
          offer_code: "GH_CR2",
          idempotency_key: "campaign-run",
          error: null,
          created_at: "2026-07-29T10:00:00Z",
          updated_at: "2026-07-29T10:01:00Z",
        },
      ],
      total: 1,
    },
    error: null,
    isError: false,
    isLoading: false,
    refetch: mocks.listRefetch,
  }),
}));

vi.mock("@/components/ui/Toast", () => ({
  toast: {
    error: mocks.toastError,
    info: mocks.toastInfo,
    success: mocks.toastSuccess,
    warning: mocks.toastWarning,
  },
}));

import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";

const RUN_ID = "11111111-2222-3333-4444-555555555555";

function detail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: RUN_ID,
    preset_id: null,
    status: "creating",
    config: {},
    progress: { stage: "creating" },
    created_meta_ids: {},
    error: null,
    idempotency_key: "campaign-run",
    created_at: "2026-07-29T10:00:00Z",
    updated_at: "2026-07-29T10:01:00Z",
    task: {
      id: 1842,
      state: "running",
      queue_status: "running",
      outcome: null,
      attempt_count: 1,
      max_attempts: 3,
      external_started: false,
      cancel_requested_at: null,
      deadline_at: "2026-07-29T10:03:00Z",
      correlation_id: "correlation-id",
      result: null,
    },
    controls: {
      abort: { available: true, reason: "abort_available" },
      resume: { available: false, reason: "run_not_terminal" },
    },
    ...overrides,
  };
}

async function openRun(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Развернуть детали" }));
}

describe("CampaignRunsHistory command lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.localStorage.clear();
    mocks.detail = detail();
    mocks.detailRefetch.mockResolvedValue({ data: mocks.detail });
    mocks.listRefetch.mockResolvedValue({ data: undefined });
    mocks.abort.mockResolvedValue({
      action: "abort",
      run_id: RUN_ID,
      task_id: 1842,
      state: "queued",
      run_status: "creating",
      created: true,
      correlation_id: "correlation-id",
      reason: "cooperative_abort_requested",
    });
    mocks.resume.mockResolvedValue({
      action: "resume",
      run_id: RUN_ID,
      task_id: 1843,
      state: "queued",
      run_status: "queued",
      created: true,
      correlation_id: "correlation-id",
      reason: "resume_queued",
    });
  });

  it("submits authoritative abort on the second tap and treats 202/queued as pending", async () => {
    const user = userEvent.setup();
    render(<CampaignRunsHistory />);

    await openRun(user);
    expect(screen.getByText("Выполняется")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Запросить остановку" }));

    await waitFor(() => expect(mocks.abort).toHaveBeenCalledOnce());
    const request = mocks.abort.mock.calls[0]?.[0] as {
      params: { header: Record<string, string>; path: { run_id: string } };
    };
    expect(request.params.path.run_id).toBe(RUN_ID);
    expect(request.params.header["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/i);
    expect(
      await screen.findByText("Остановка поставлена в очередь. Завершение ещё не подтверждено."),
    ).toBeVisible();
    expect(mocks.toastInfo).toHaveBeenCalled();
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(mocks.detailRefetch).toHaveBeenCalledOnce();
      expect(mocks.listRefetch).toHaveBeenCalledOnce();
    });
  });

  it("renders only server-authorized controls and explains unavailable actions in human terms", async () => {
    mocks.detail = detail({
      status: "failed",
      controls: {
        abort: { available: false, reason: "run_already_failed" },
        resume: {
          available: true,
          reason: "pre_external_checkpoint_available",
        },
      },
    });
    const user = userEvent.setup();
    render(<CampaignRunsHistory />);

    await openRun(user);
    expect(screen.queryByRole("button", { name: "Запросить остановку" })).toBeNull();
    expect(screen.getByText("Запуск уже завершился ошибкой.")).toBeVisible();
    expect(screen.queryByText("run_already_failed")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Безопасно повторить" }));
    await waitFor(() => expect(mocks.resume).toHaveBeenCalledOnce());
    expect(mocks.abort).not.toHaveBeenCalled();
  });

  it("keeps UNKNOWN visibly distinct from a confirmed result", async () => {
    mocks.detail = detail({
      task: {
        id: 1842,
        state: "unknown",
        queue_status: "failed",
        outcome: "UNKNOWN",
        attempt_count: 1,
        max_attempts: 3,
        external_started: true,
        cancel_requested_at: null,
        deadline_at: null,
        correlation_id: "correlation-id",
        result: { reconcile_required: true },
      },
      controls: {
        abort: {
          available: false,
          reason: "run_task_state_inconsistent",
        },
        resume: {
          available: false,
          reason: "external_boundary_crossed",
        },
      },
    });
    const user = userEvent.setup();
    render(<CampaignRunsHistory />);

    await openRun(user);
    expect(screen.getByText("Результат неизвестен")).toBeVisible();
    expect(screen.getByText(/Не повторяйте запуск до ручной сверки/)).toBeVisible();
    expect(
      screen.getByText("Повтор заблокирован: задача могла начать изменения в Meta."),
    ).toBeVisible();
    expect(screen.queryByText("UNKNOWN")).toBeNull();
  });

  it("reuses one idempotency key when an ambiguous request is replayed", async () => {
    mocks.abort
      .mockRejectedValueOnce(new TypeError("network response lost"))
      .mockResolvedValueOnce({
        action: "abort",
        run_id: RUN_ID,
        task_id: 1842,
        state: "running",
        run_status: "creating",
        created: false,
        correlation_id: "correlation-id",
        reason: "cooperative_abort_requested",
      });
    const user = userEvent.setup();
    render(<CampaignRunsHistory />);

    await openRun(user);
    const button = screen.getByRole("button", {
      name: "Запросить остановку",
    });
    await user.click(button);
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledTimes(1));
    await user.click(button);
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledTimes(2));

    const first = mocks.abort.mock.calls[0]?.[0] as {
      params: { header: Record<string, string> };
    };
    const replay = mocks.abort.mock.calls[1]?.[0] as {
      params: { header: Record<string, string> };
    };
    expect(replay.params.header["Idempotency-Key"]).toBe(first.params.header["Idempotency-Key"]);
    expect(
      await screen.findByText("Показано сохранённое состояние уже принятой команды."),
    ).toBeVisible();
    expect(
      screen.getByText("Остановка выполняется. Завершение ещё не подтверждено."),
    ).toBeVisible();
  });
});
