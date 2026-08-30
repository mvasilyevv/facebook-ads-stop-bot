import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  abort: vi.fn(),
  detail: null as Record<string, unknown> | null,
  hapticImpact: vi.fn(),
  hapticNotify: vi.fn(),
  hapticSelection: vi.fn(),
  openLink: vi.fn(),
  refetchDetail: vi.fn(),
  refetchRuns: vi.fn(),
  resume: vi.fn(),
  summaryError: null as string | null,
  summaryStatus: "queued",
}));

vi.mock("@/lib/operatorApi", () => ({
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
  useRunsHistory: () => ({
    data: {
      pages: [
        {
          runs: [
            {
              id: "11111111-2222-3333-4444-555555555555",
              preset_id: null,
              status: mocks.summaryStatus,
              offer_code: "GH_CR2",
              idempotency_key: "run-key",
              error: mocks.summaryError,
              created_at: "2026-07-21T10:00:00Z",
              updated_at: "2026-07-21T10:01:00Z",
            },
          ],
          total: 1,
          offset: 0,
          limit: 50,
        },
      ],
    },
    error: null,
    isError: false,
    isFetching: false,
    isLoading: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: mocks.refetchRuns,
  }),
  useCampaignRun: () => ({
    data: mocks.detail,
    error: null,
    isError: false,
    isLoading: false,
    refetch: mocks.refetchDetail,
  }),
  useAbortCampaignRun: () => ({
    error: null,
    isError: false,
    isPending: false,
    mutateAsync: mocks.abort,
    variables: null,
  }),
  useResumeCampaignRun: () => ({
    error: null,
    isError: false,
    isPending: false,
    mutateAsync: mocks.resume,
    variables: null,
  }),
}));

vi.mock("@/lib/tg", () => ({
  haptic: {
    impact: mocks.hapticImpact,
    notify: mocks.hapticNotify,
    selection: mocks.hapticSelection,
  },
  openLink: mocks.openLink,
}));

import { RunsHistory } from "@/routes/campaigns/RunsHistory";

const CREATING_RUN = {
  id: "11111111-2222-3333-4444-555555555555",
  preset_id: null,
  status: "creating",
  config: { offer_code: "GH_CR2" },
  progress: { stage: "creating", completed: 2, total: 3 },
  created_meta_ids: { campaigns: ["1"], adsets: ["2", "3"] },
  failure_class: null,
  idempotency_key: "run-key",
  created_at: "2026-07-21T10:00:00Z",
  updated_at: "2026-07-21T10:02:00Z",
  task: {
    id: 1842,
    state: "running",
    queue_status: "running",
    outcome: null,
    attempt_count: 1,
    max_attempts: 3,
    external_started: false,
    cancel_requested_at: null,
    deadline_at: "2026-07-21T10:05:00Z",
  },
  controls: {
    abort: { available: true, reason: "abort_available" },
    resume: { available: false, reason: "run_not_terminal" },
  },
};

describe("TMA campaign runs progress-only surface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.localStorage.clear();
    mocks.detail = CREATING_RUN;
    mocks.summaryError = null;
    mocks.summaryStatus = "queued";
    mocks.abort.mockResolvedValue({
      action: "abort",
      run_id: CREATING_RUN.id,
      task_id: 1842,
      state: "queued",
      run_status: "creating",
      created: true,
      correlation_id: "correlation-id",
      reason: "cooperative_abort_requested",
    });
    mocks.resume.mockResolvedValue({
      action: "resume",
      run_id: CREATING_RUN.id,
      task_id: 1843,
      state: "queued",
      run_status: "queued",
      created: true,
      correlation_id: "correlation-id",
      reason: "resume_queued",
    });
    mocks.refetchDetail.mockResolvedValue(undefined);
    mocks.refetchRuns.mockResolvedValue(undefined);
  });

  it("shows progress and results without exposing clone or cleanup controls", () => {
    render(<RunsHistory />);

    expect(screen.getByText("Ход выполнения")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /запустить|клонировать|cleanup/i }),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );
    expect(
      screen.getByRole("region", { name: "Детали запуска" }),
    ).toBeVisible();
    expect(screen.getAllByText("Создание").length).toBeGreaterThan(0);
    expect(screen.getByText("Кампании: 1")).toBeVisible();
    expect(screen.getByText("Группы: 2")).toBeVisible();
    expect(screen.getByText("Всего").parentElement).toHaveClass("col-span-2");
  });

  it("submits cooperative abort on the second tap and keeps queued distinct from success", async () => {
    render(<RunsHistory />);
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Запросить остановку" }),
    );
    await waitFor(() =>
      expect(mocks.abort).toHaveBeenCalledWith({
        params: {
          path: { run_id: "11111111-2222-3333-4444-555555555555" },
          header: {
            "Idempotency-Key": expect.stringMatching(/^[0-9a-f-]{36}$/i),
          },
        },
      }),
    );
    expect(
      await screen.findByText(
        "Остановка поставлена в очередь. Завершение ещё не подтверждено.",
      ),
    ).toBeVisible();
    expect(mocks.hapticNotify).toHaveBeenCalledWith("warning");
    expect(mocks.hapticNotify).not.toHaveBeenCalledWith("success");
    expect(mocks.refetchDetail).toHaveBeenCalledOnce();
    expect(mocks.refetchRuns).toHaveBeenCalledOnce();
  });

  it("renders only available controls and explains unavailable reasons without raw codes", async () => {
    mocks.detail = {
      ...CREATING_RUN,
      status: "failed",
      failure_class: "safe_retry",
      created_meta_ids: {},
      task: {
        ...CREATING_RUN.task,
        state: "failed",
        queue_status: "failed",
        outcome: "REJECTED",
      },
      controls: {
        abort: { available: false, reason: "run_already_failed" },
        resume: {
          available: true,
          reason: "pre_external_checkpoint_available",
        },
      },
    };
    render(<RunsHistory />);
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );

    expect(
      screen.queryByRole("button", { name: "Запросить остановку" }),
    ).toBeNull();
    expect(screen.getByText("Запуск уже завершился ошибкой.")).toBeVisible();
    expect(screen.getByText("Доступен безопасный повтор")).toBeVisible();
    expect(screen.queryByText("run_already_failed")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Безопасно повторить" }),
    );
    await waitFor(() => expect(mocks.resume).toHaveBeenCalledOnce());
    expect(mocks.abort).not.toHaveBeenCalled();
  });

  it("renders UNKNOWN as an explicit non-green lifecycle", () => {
    mocks.detail = {
      ...CREATING_RUN,
      task: {
        ...CREATING_RUN.task,
        state: "unknown",
        queue_status: "failed",
        outcome: "UNKNOWN",
        external_started: true,
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
    };
    render(<RunsHistory />);
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );

    expect(screen.getByText("Результат неизвестен")).toBeVisible();
    expect(
      screen.getByText(/Не повторяйте запуск до ручной сверки/),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Повтор заблокирован: задача могла начать изменения в Meta.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("UNKNOWN")).toBeNull();
    expect(screen.queryByText("Задача #1842")).toBeNull();
  });

  it("replays an ambiguous abort with the same durable idempotency key", async () => {
    mocks.abort
      .mockRejectedValueOnce(new TypeError("network response lost"))
      .mockResolvedValueOnce({
        action: "abort",
        run_id: CREATING_RUN.id,
        task_id: 1842,
        state: "running",
        run_status: "creating",
        created: false,
        correlation_id: "correlation-id",
        reason: "cooperative_abort_requested",
      });
    render(<RunsHistory />);
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );
    const button = screen.getByRole("button", {
      name: "Запросить остановку",
    });
    fireEvent.click(button);
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledTimes(1));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledTimes(2));

    const first = mocks.abort.mock.calls[0]?.[0] as {
      params: { header: Record<string, string> };
    };
    const replay = mocks.abort.mock.calls[1]?.[0] as {
      params: { header: Record<string, string> };
    };
    expect(replay.params.header["Idempotency-Key"]).toBe(
      first.params.header["Idempotency-Key"],
    );
    expect(
      await screen.findByText(
        "Показано сохранённое состояние уже принятой команды.",
      ),
    ).toBeVisible();
  });

  it("shows an action-required partial state with IDs and safe Ads Manager link", () => {
    mocks.detail = {
      ...CREATING_RUN,
      status: "failed",
      failure_class: "manual_review",
      progress: {
        stage: "failed",
        completed: 2,
        total: 3,
        outcome: "UNKNOWN",
        reason: "partial_or_ack_lost",
        internal_trace: "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f",
      },
      created_meta_ids: {
        campaigns: ["101"],
        adsets: ["201", "202"],
        ads: ["301"],
        creatives: [],
      },
      error: "partial_fail: проверь Meta вручную",
      task: {
        ...CREATING_RUN.task,
        state: "unknown",
        queue_status: "failed",
        outcome: "UNKNOWN",
        external_started: true,
        result: { exception: "database password" },
      },
    };

    render(<RunsHistory />);
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть запуск GH_CR2" }),
    );

    const alert = screen.getByRole("alert", {
      name: "Требуется ручная сверка",
    });
    expect(alert).toHaveTextContent("Не повторяйте запуск");
    expect(alert).toHaveTextContent("Кампании · 1");
    expect(alert).toHaveTextContent("101");
    expect(alert).toHaveTextContent("Группы · 2");
    expect(
      screen.queryByRole("button", { name: /cleanup|удалить/i }),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Открыть Ads Manager" }),
    );
    expect(mocks.openLink).toHaveBeenCalledWith(
      "https://www.facebook.com/adsmanager/manage/campaigns?ids=101",
    );
    expect(
      screen.getByText(/Meta могла принять часть изменений/),
    ).toBeVisible();
    expect(
      screen.queryByText(
        /partial_fail|partial_or_ack_lost|internal_trace|database password/,
      ),
    ).toBeNull();
    expect(
      screen.queryByText("8b8d0c93-15dc-46b4-8fe0-8da6bec3667f"),
    ).toBeNull();
    expect(screen.queryByText("Задача #1842")).toBeNull();
  });

  it("replaces list-level backend errors with a localized diagnostic", () => {
    mocks.summaryStatus = "failed";
    mocks.summaryError = "Traceback: database password leaked";

    render(<RunsHistory />);

    expect(
      screen.getByText(
        "Запуск завершился ошибкой. Откройте детали для безопасных действий.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/Traceback|database password/)).toBeNull();
  });
});
