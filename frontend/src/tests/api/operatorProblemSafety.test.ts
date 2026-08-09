import { describe, expect, it } from "vitest";

import { GeneratedApiError, safeApiProblemMessage } from "@fb/operator-api";

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
});
