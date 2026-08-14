import { describe, expect, it } from "vitest";

import {
  GeneratedApiError,
  apiProblemMessage,
  safeApiProblemMessage,
} from "@fb/operator-api";

import { operatorProblemMessage } from "@/lib/api/operator";

describe("operator-visible API problems", () => {
  it("keeps correlation ids and raw exceptions out of incident copy", () => {
    const correlationId = "00000000-0000-0000-0000-000000000099";
    const problem = {
      code: "incident_ack_unavailable",
      message: "Подтверждение временно недоступно",
      correlation_id: correlationId,
      field_errors: null,
    };

    expect(safeApiProblemMessage(problem)).toBe(problem.message);
    expect(safeApiProblemMessage(new GeneratedApiError(503, problem))).toBe(problem.message);
    expect(safeApiProblemMessage(new Error("traceback: database host"))).toBe(
      "Данные временно недоступны",
    );
    expect(safeApiProblemMessage(problem)).not.toContain(correlationId);
  });

  it("never appends a correlation reference to the diagnostic message either", () => {
    const correlationId = "00000000-0000-0000-0000-000000000042";
    const problem = {
      code: "command_rejected",
      message: "Команда отклонена",
      correlation_id: correlationId,
      field_errors: null,
    };

    // GeneratedApiError.message доходит до рендера через сторонние обработчики,
    // поэтому UUID не дописывается даже в диагностическую копию.
    expect(apiProblemMessage(problem)).toBe("Команда отклонена");
    expect(apiProblemMessage(problem)).not.toContain(correlationId);
    expect(new GeneratedApiError(409, problem).message).not.toContain(correlationId);
  });

  it("keeps operatorProblemMessage free of raw exceptions and identifiers", () => {
    const correlationId = "00000000-0000-0000-0000-000000000077";

    const recovery =
      "Сервер не подтвердил данные. Повторите попытку; " +
      "если не помогает — откройте «Источники и воркеры».";
    const leaky = operatorProblemMessage(
      new Error("Traceback: postgres://user:pw@db-01 secret token"),
    );
    expect(leaky).toBe(recovery);
    expect(leaky).not.toMatch(/Traceback|postgres:\/\/|secret/);
    expect(
      operatorProblemMessage(
        new GeneratedApiError(503, {
          code: "snapshot_unavailable",
          message: "Снимок временно недоступен",
          correlation_id: correlationId,
          field_errors: null,
        }),
      ),
    ).toBe("Снимок временно недоступен");
    expect(operatorProblemMessage({ detail: "internal worker crash" })).toBe(recovery);
  });
});
