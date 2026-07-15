// Тесты AssistantWidget — плавающий AI-ассистент (кнопка/панель/лента/инпут/WS-нотификации).
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

describe("AssistantWidget — WS-нотификации алертов", () => {
  // pushNotification при закрытой панели растит unread-бейдж на кнопке; открытие
  // панели сбрасывает unread, а сама нотификация видна в ленте со стилем stage=stop.
  it("бейдж растёт при закрытой панели, открытие сбрасывает unread, нотификация видна", async () => {
    const user = userEvent.setup();
    render(<AssistantWidget />);

    act(() => {
      useChatWidget.getState().pushNotification({
        fb_ad_id: "123456",
        ad_name: "GH_CR2 | 15.07",
        offer_code: "GH_CR2",
        stage: "stop",
        matched_rule_codes: ["cpa_stop"],
      });
    });

    const closedButton = screen.getByRole("button", { name: "Открыть AI-ассистента" });
    expect(within(closedButton).getByText("1")).toBeInTheDocument();

    await user.click(closedButton);

    // Панель открыта — бейдж на кнопке больше не показывается (unread сброшен).
    const openButton = screen.getByRole("button", { name: "Закрыть AI-ассистента" });
    expect(within(openButton).queryByText("1")).not.toBeInTheDocument();

    expect(screen.getByText(/STOP: GH_CR2 \| 15\.07 \[GH_CR2\] — cpa_stop/)).toBeInTheDocument();
  });

  // Несколько нотификаций подряд при закрытой панели суммируются в счётчике.
  it("несколько нотификаций подряд суммируют unread", () => {
    render(<AssistantWidget />);

    act(() => {
      useChatWidget.getState().pushNotification({ stage: "warning", ad_name: "A" });
      useChatWidget.getState().pushNotification({ stage: "stop", ad_name: "B" });
    });

    const btn = screen.getByRole("button", { name: "Открыть AI-ассистента" });
    expect(within(btn).getByText("2")).toBeInTheDocument();
  });
});

describe("AssistantWidget — нотификации не попадают в тело запроса", () => {
  // Нотификация в ленте (kind: "notification") не должна уйти в /ai/chat вместе
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

    useChatWidget.getState().pushNotification({
      stage: "warning",
      ad_name: "Шумный алерт",
      matched_rule_codes: ["cpm_warning"],
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
