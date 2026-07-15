// Тесты AssistantWidget — плавающий AI-ассистент (кнопка/панель/лента/инпут/почасовой пульс).
// Fetch стабится вручную (vi.stubGlobal), как в tests/client.test.ts — MSW в проекте нет.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Мок auth store — без apiKey (модуль-уровень, hoisted корректно), как в tests/client.test.ts.
vi.mock("@/stores/auth", () => ({
  useAuthStore: { getState: () => ({ apiKey: null }) },
}));

import { AssistantWidget } from "@/components/domain/assistant/AssistantWidget";
import { useChatWidget } from "@/stores/chatWidget";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function resetStore() {
  useChatWidget.setState({
    open: false,
    unread: 0,
    messages: [],
    pending: false,
    lastModel: null,
    lastPulseHour: null,
  });
}

beforeEach(() => {
  resetStore();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Открыть AI-ассистента" }));
}

describe("AssistantWidget — кнопка и панель", () => {
  // Кнопка рендерится закрытой; клик открывает панель; Esc закрывает её обратно.
  it("клик открывает панель, Esc закрывает", async () => {
    const user = userEvent.setup();
    render(<AssistantWidget />);

    expect(screen.getByRole("button", { name: "Открыть AI-ассистента" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "AI-ассистент" })).not.toBeInTheDocument();

    await openPanel(user);
    expect(screen.getByRole("dialog", { name: "AI-ассистент" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "AI-ассистент" })).not.toBeInTheDocument();
  });
});

describe("AssistantWidget — отправка сообщения", () => {
  // Стаб fetch отвечает после клика → ответ ассистента появляется в ленте;
  // пока идёт запрос, инпут disabled.
  it("ответ ассистента появляется в ленте, инпут disabled во время pending", async () => {
    let resolveFetch!: (v: Response) => void;
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(fetchPromise));

    const user = userEvent.setup();
    render(<AssistantWidget />);
    await openPanel(user);

    const textarea = screen.getByLabelText("Сообщение ассистенту");
    await user.type(textarea, "Как дела с алертами?");
    await user.click(screen.getByRole("button", { name: "Отправить сообщение" }));

    await waitFor(() => expect(textarea).toBeDisabled());

    resolveFetch(
      jsonResponse({
        answer: "Всё штатно, активных стопов нет.",
        tool_calls: [{ name: "get_recent_alerts", error: null }],
        generated_at: new Date().toISOString(),
        model: "gpt-5.6-luna",
      }),
    );

    expect(await screen.findByText("Всё штатно, активных стопов нет.")).toBeInTheDocument();
    expect(screen.getByText(/проверил:/)).toBeInTheDocument();
    expect(screen.getByText("get_recent_alerts")).toBeInTheDocument();
    await waitFor(() => expect(textarea).not.toBeDisabled());
  });

  // Enter в textarea отправляет сообщение (Shift+Enter — перенос, НЕ отправка).
  it("Enter отправляет сообщение, Shift+Enter — нет", async () => {
    const user = userEvent.setup();
    render(<AssistantWidget />);
    await openPanel(user);
    const fetchSpy = vi.fn().mockResolvedValue(
      jsonResponse({
        answer: "ок",
        tool_calls: [],
        generated_at: new Date().toISOString(),
        model: "gpt-5.6-luna",
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const textarea = screen.getByLabelText("Сообщение ассистенту");
    await user.type(textarea, "строка{Shift>}{Enter}{/Shift}ещё");
    expect(fetchSpy).not.toHaveBeenCalled(); // Shift+Enter не отправил

    await user.keyboard("{Enter}");
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("ок")).toBeInTheDocument();
  });

  // 429 (лимит 30/час) → в ленте появляется сообщение про лимит, не тост.
  it("429 показывает сообщение о лимите в ленте", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "rate limited" }, 429)),
    );

    const user = userEvent.setup();
    render(<AssistantWidget />);
    await openPanel(user);

    await user.type(screen.getByLabelText("Сообщение ассистенту"), "вопрос");
    await user.click(screen.getByRole("button", { name: "Отправить сообщение" }));

    expect(
      await screen.findByText("Лимит запросов исчерпан, попробуй позже"),
    ).toBeInTheDocument();
  });

  // 503 (AI не настроен) → в ленте сообщение с текстом detail, не тост.
  it("503 показывает 'Ассистент недоступен: <detail>' в ленте", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "AI-провайдеры не настроены" }, 503)),
    );

    const user = userEvent.setup();
    render(<AssistantWidget />);
    await openPanel(user);

    await user.type(screen.getByLabelText("Сообщение ассистенту"), "вопрос");
    await user.click(screen.getByRole("button", { name: "Отправить сообщение" }));

    expect(
      await screen.findByText("Ассистент недоступен: AI-провайдеры не настроены"),
    ).toBeInTheDocument();
  });
});

describe("AssistantWidget — почасовой пульс", () => {
  // fetchPulse при important=true кладёт пульс-сообщение «📟 Пульс кабинета» в ленту
  // и растит unread-бейдж на закрытой кнопке; открытие панели сбрасывает unread.
  it("important=true → сообщение пульса в ленте + бейдж при закрытой панели", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          important: true,
          text: "2 стопа за час: GH_CR2 (CPA), GH_AVI (spend без лидов)",
          generated_at: "2026-07-15T12:07:00+00:00",
        }),
      ),
    );

    const user = userEvent.setup();
    render(<AssistantWidget />);

    await act(async () => {
      await useChatWidget.getState().fetchPulse();
    });

    const closedButton = screen.getByRole("button", { name: "Открыть AI-ассистента" });
    expect(within(closedButton).getByText("1")).toBeInTheDocument();

    await user.click(closedButton);

    // Панель открыта — бейдж на кнопке больше не показывается (unread сброшен).
    const openButton = screen.getByRole("button", { name: "Закрыть AI-ассистента" });
    expect(within(openButton).queryByText("1")).not.toBeInTheDocument();

    expect(screen.getByText(/Пульс кабинета/)).toBeInTheDocument();
    expect(
      screen.getByText(/2 стопа за час: GH_CR2 \(CPA\), GH_AVI \(spend без лидов\)/),
    ).toBeInTheDocument();
  });

  // important=false (тихий час) → в ленте пусто, бейджа нет — виджет молчит.
  it("important=false → лента пуста, бейджа нет", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          important: false,
          text: null,
          generated_at: "2026-07-15T12:07:00+00:00",
        }),
      ),
    );

    render(<AssistantWidget />);

    await act(async () => {
      await useChatWidget.getState().fetchPulse();
    });

    expect(useChatWidget.getState().messages).toHaveLength(0);
    const btn = screen.getByRole("button", { name: "Открыть AI-ассистента" });
    expect(within(btn).queryByText("1")).not.toBeInTheDocument();
  });

  // Повторный fetchPulse того же календарного часа (тот же generated_at из серверного
  // кэша) не дублирует сообщение в ленте — дедуп через lastPulseHour.
  it("повторный fetchPulse того же часа не дублирует сообщение", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          important: true,
          text: "1 стоп за час: GH_CR2",
          generated_at: "2026-07-15T12:07:00+00:00",
        }),
      ),
    );

    render(<AssistantWidget />);

    await act(async () => {
      await useChatWidget.getState().fetchPulse();
      await useChatWidget.getState().fetchPulse();
    });

    expect(useChatWidget.getState().messages).toHaveLength(1);
    expect(useChatWidget.getState().unread).toBe(1);
  });

  // Ошибка сети/бэка при опросе пульса — тихая: ни сообщения, ни бейджа, ни краша.
  it("ошибка fetchPulse — молчание без сообщений и бейджа", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    render(<AssistantWidget />);

    await act(async () => {
      await useChatWidget.getState().fetchPulse();
    });

    expect(useChatWidget.getState().messages).toHaveLength(0);
    expect(useChatWidget.getState().unread).toBe(0);
    warnSpy.mockRestore();
  });
});

describe("AssistantWidget — пульсы не попадают в тело запроса", () => {
  // Пульс-сообщение в ленте (kind: "notification") не должно уйти в /ai/chat вместе
  // с вопросом пользователя — иначе ассистент "отвечал" бы на собственные пуши.
  it("тело запроса содержит только user/assistant сообщения", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        answer: "ок",
        tool_calls: [],
        generated_at: new Date().toISOString(),
        model: "m",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    act(() => {
      useChatWidget.getState().pushPulse("Шумный пульс за час", "2026-07-15T12:07:00+00:00");
    });

    const user = userEvent.setup();
    render(<AssistantWidget />);
    await openPanel(user);

    await user.type(screen.getByLabelText("Сообщение ассистенту"), "привет");
    await user.click(screen.getByRole("button", { name: "Отправить сообщение" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(options.body as string) as { messages: unknown[] };
    expect(body.messages).toEqual([{ role: "user", content: "привет" }]);
  });
});
