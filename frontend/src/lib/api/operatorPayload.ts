import {
  OperatorPayloadValidationError,
  validateOperatorPayload as validateSharedOperatorPayload,
} from "@fb/shared/operator/runtimeValidation";

import { invalidApiPayload } from "./client";

/** Convert the shared runtime guard into the web client's stable API error. */
export function validateOperatorPayload(path: string, value: unknown): unknown {
  try {
    return validateSharedOperatorPayload(path, value);
  } catch (error) {
    if (error instanceof OperatorPayloadValidationError) {
      throw invalidApiPayload(path, {
        field: error.field,
        payload: value,
      });
    }
    throw error;
  }
}
