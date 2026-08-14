import { describe, expect, it } from "vitest";

import {
  apiProblemMessage,
  dataOrThrow,
  isApiProblem,
  noContentOrThrow,
} from "@fb/operator-api";

function response(status: number): Response {
  return new Response(null, { status });
}

describe("generated API result helpers", () => {
  it("preserves valid falsy success data", async () => {
    await expect(
      dataOrThrow(Promise.resolve({ data: 0, response: response(200) })),
    ).resolves.toBe(0);
    await expect(
      dataOrThrow(Promise.resolve({ data: false, response: response(200) })),
    ).resolves.toBe(false);
  });

  it("throws the canonical ApiProblem and keeps the correlation id out of the message", async () => {
    const problem = {
      code: "COMMAND_REJECTED",
      message: "Команда отклонена",
      correlation_id: "corr-409",
      field_errors: null,
    };

    await expect(
      dataOrThrow(
        Promise.resolve({
          error: problem,
          response: response(409),
        }),
      ),
    ).rejects.toMatchObject({
      name: "GeneratedApiError",
      status: 409,
      payload: problem,
      // correlation_id остаётся в payload для диагностики, но не дописывается
      // в message: этот текст доходит до operator UI и Telegram.
      message: "Команда отклонена",
    });
    expect(apiProblemMessage(problem)).not.toContain("corr-409");
  });

  it("accepts generated 204 responses without inventing a body", async () => {
    await expect(
      noContentOrThrow(Promise.resolve({ response: response(204) })),
    ).resolves.toBeUndefined();
  });

  it("fails closed when an OK response omits required typed data", async () => {
    await expect(
      dataOrThrow(Promise.resolve({ response: response(200) })),
    ).rejects.toMatchObject({
      name: "GeneratedApiError",
      status: 200,
      message: "Ошибка API 200",
    });
  });

  it("recognizes only the canonical ApiProblem shape", () => {
    const problem = {
      code: "VALIDATION_ERROR",
      message: "Некорректный запрос",
      correlation_id: "",
      field_errors: { account_id: ["required"] },
    };

    expect(isApiProblem(problem)).toBe(true);
    expect(apiProblemMessage(problem)).toBe("Некорректный запрос");
    expect(isApiProblem({ detail: "legacy error" })).toBe(false);
    expect(apiProblemMessage({ detail: "legacy error" }, "Ошибка")).toBe(
      "Ошибка",
    );
  });
});
