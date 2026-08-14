/**
 * В mini текст ошибки уходит в Telegram через tgAlert. Сырой exception,
 * traceback или correlation UUID там недопустимы: сообщение видит оператор
 * в чате и не может его отозвать. Единственный разрешённый источник копии —
 * safeApiProblemMessage.
 */
import { describe, expect, it, vi } from "vitest";

import { GeneratedApiError } from "@fb/operator-api";

vi.mock("@/lib/tg", () => ({
  getInitData: () => "signed_init_data",
}));

import { operatorProblemMessage } from "@/lib/operatorApi";

describe("mini operator problem copy", () => {
  it("replaces raw exceptions with recovery copy", () => {
    const recovery = "Сервер не подтвердил данные. Повторите попытку.";
    const leaky = operatorProblemMessage(
      new Error("Traceback: postgres://user:pw@db-01 bot_token=123:AAE"),
    );
    expect(leaky).toBe(recovery);
    // Ни один фрагмент исходного исключения не должен просочиться наружу:
    // этот текст уходит в Telegram.
    expect(leaky).not.toMatch(/Traceback|postgres:\/\/|bot_token/);
    expect(operatorProblemMessage({ detail: "internal worker crash" })).toBe(recovery);
  });

  it("keeps the correlation id out of the Telegram-visible message", () => {
    const correlationId = "00000000-0000-0000-0000-000000000123";
    const message = operatorProblemMessage(
      new GeneratedApiError(503, {
        code: "snapshot_unavailable",
        message: "Снимок временно недоступен",
        correlation_id: correlationId,
        field_errors: null,
      }),
    );

    expect(message).toBe("Снимок временно недоступен");
    expect(message).not.toContain(correlationId);
    expect(message).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });
});
