import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  claimAd,
  confirmDraftTask,
  disableAd,
  fetchJson,
  rejectDraftTask,
  setTelegramWebAppUrl,
  snoozeAd,
} from "../api.js";

function ok(body) {
  return { ok: true, status: 200, json: async () => body };
}

// api.js: проверяем URL/метод/тело TMA-действий и поведение fetchJson.
describe("api.js TMA-действия", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("tma_token", "TESTTOKEN");
  });

  // fetchJson подставляет Authorization: Bearer из хранилища
  it("fetchJson добавляет Bearer-заголовок", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true }));
    await fetchJson("/x");
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer TESTTOKEN");
  });

  // disable шлёт POST /tma/ads/{id}/disable с reason
  it("disableAd → POST disable c reason", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true, channel: "meta_api" }));
    await disableAd("AD1", "дорого");
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toContain("/tma/ads/AD1/disable");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body).reason).toBe("дорого");
  });

  // claim шлёт POST /tma/ads/{id}/claim
  it("claimAd → POST claim", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true, alert_state: "claimed" }));
    const r = await claimAd("AD2");
    expect(global.fetch.mock.calls[0][0]).toContain("/tma/ads/AD2/claim");
    expect(r.alert_state).toBe("claimed");
  });

  // snooze шлёт POST /tma/ads/{id}/snooze c minutes
  it("snoozeAd → POST snooze c minutes", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true, snoozed_until: "2026-01-01" }));
    await snoozeAd("AD3", 60);
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toContain("/tma/ads/AD3/snooze");
    expect(JSON.parse(opts.body).minutes).toBe(60);
  });

  // confirm draft → POST /tma/draft-tasks/{id}/confirm
  it("confirmDraftTask → POST confirm", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true }));
    await confirmDraftTask(42);
    expect(global.fetch.mock.calls[0][0]).toContain("/tma/draft-tasks/42/confirm");
  });

  // reject draft → POST /tma/draft-tasks/{id}/reject c reason
  it("rejectDraftTask → POST reject c reason", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ ok: true }));
    await rejectDraftTask(7, "не надо");
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toContain("/tma/draft-tasks/7/reject");
    expect(JSON.parse(opts.body).reason).toBe("не надо");
  });

  // web_app_url → PUT /settings/telegram/web-app-url
  it("setTelegramWebAppUrl → PUT web-app-url", async () => {
    global.fetch = vi.fn().mockResolvedValue(ok({ web_app_url: "https://x/" }));
    await setTelegramWebAppUrl("https://x/");
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toContain("/settings/telegram/web-app-url");
    expect(opts.method).toBe("PUT");
  });

  // !ok (не 401) → Error с detail, без ретрая
  it("fetchJson бросает Error с detail при 409", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "конфликт" }) });
    await expect(fetchJson("/y")).rejects.toThrow("конфликт");
  });

  // 401 → перевыпуск токена через /tma/auth + один повтор запроса
  it("fetchJson при 401 перевыпускает токен и повторяет", async () => {
    window.Telegram = { WebApp: { initData: "x" } };
    let zCalls = 0;
    global.fetch = vi.fn().mockImplementation((url) => {
      if (String(url).includes("/tma/auth")) {
        return Promise.resolve(ok({ token: "new", role: "owner" }));
      }
      zCalls += 1;
      if (zCalls === 1) {
        return Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
      }
      return Promise.resolve(ok({ ok: true, retried: true }));
    });
    const r = await fetchJson("/z");
    expect(r.retried).toBe(true);
  });
});
