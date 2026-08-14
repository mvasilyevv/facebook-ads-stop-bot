import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
  isOperatorCommandIntentStorageError,
  OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE,
} from "../commandIntent";

const originalLocalStorage = Object.getOwnPropertyDescriptor(
  globalThis,
  "localStorage",
);

describe("operator command intent", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "localStorage", {
      value: makeStorage(),
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalLocalStorage) {
      Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
    } else {
      Reflect.deleteProperty(globalThis, "localStorage");
    }
  });

  it("reuses one key until the server returns its receipt", () => {
    const first = getOrCreateOperatorCommandIntent("pause_ad", "receipt-ad");
    const retry = getOrCreateOperatorCommandIntent("pause_ad", "receipt-ad");
    expect(retry).toBe(first);

    completeOperatorCommandIntent("pause_ad", "receipt-ad", first);
    expect(getOrCreateOperatorCommandIntent("pause_ad", "receipt-ad")).not.toBe(
      first,
    );
  });

  it("scopes keys by action and target", () => {
    const pause = getOrCreateOperatorCommandIntent("pause_ad", "scope-ad-1");
    expect(
      getOrCreateOperatorCommandIntent("activate_ad", "scope-ad-1"),
    ).not.toBe(pause);
    expect(getOrCreateOperatorCommandIntent("pause_ad", "scope-ad-2")).not.toBe(
      pause,
    );
  });

  it("keeps abort and resume campaign intents durable and independently scoped", () => {
    const abort = getOrCreateOperatorCommandIntent(
      "abort_campaign_run",
      "campaign-run-1",
    );
    expect(
      getOrCreateOperatorCommandIntent("abort_campaign_run", "campaign-run-1"),
    ).toBe(abort);
    expect(
      getOrCreateOperatorCommandIntent("resume_campaign_run", "campaign-run-1"),
    ).not.toBe(abort);
  });

  it("keeps a retry-scan key durable until the command receipt arrives", () => {
    const first = getOrCreateOperatorCommandIntent(
      "retry_scan",
      "login-incident-1",
    );
    expect(
      getOrCreateOperatorCommandIntent("retry_scan", "login-incident-1"),
    ).toBe(first);

    completeOperatorCommandIntent("retry_scan", "login-incident-1", first);
    expect(
      getOrCreateOperatorCommandIntent("retry_scan", "login-incident-1"),
    ).not.toBe(first);
  });

  it("reuses the durable key after the command-intent module reloads", async () => {
    const first = getOrCreateOperatorCommandIntent("pause_ad", "reload-ad");

    vi.resetModules();
    const reloaded = await import("../commandIntent");

    expect(
      reloaded.getOrCreateOperatorCommandIntent("pause_ad", "reload-ad"),
    ).toBe(first);
  });

  it("fails closed with a typed error when localStorage is unavailable", () => {
    Object.defineProperty(globalThis, "localStorage", {
      value: undefined,
      configurable: true,
      writable: true,
    });

    const error = captureError(() =>
      getOrCreateOperatorCommandIntent("pause_ad", "storage-unavailable-ad"),
    );

    expect(isOperatorCommandIntentStorageError(error)).toBe(true);
    expect(error).toMatchObject({
      code: OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE,
      operation: "access",
    });
  });

  it("does not return a volatile key when durable persistence fails", () => {
    vi.spyOn(globalThis.localStorage, "setItem").mockImplementation(
      () => undefined,
    );

    const error = captureError(() =>
      getOrCreateOperatorCommandIntent("activate_ad", "storage-write-ad"),
    );

    expect(isOperatorCommandIntentStorageError(error)).toBe(true);
    expect(error).toMatchObject({
      code: OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE,
      operation: "write",
    });
    expect(
      globalThis.localStorage.getItem(
        "fb-agent:operator-command-intent:v1:activate_ad:storage-write-ad",
      ),
    ).toBeNull();
  });

  it("fails closed when a persisted intent cannot be read", () => {
    vi.spyOn(globalThis.localStorage, "getItem").mockImplementation(() => {
      throw new Error("storage access denied");
    });

    const error = captureError(() =>
      getOrCreateOperatorCommandIntent("pause_ad", "storage-read-ad"),
    );

    expect(isOperatorCommandIntentStorageError(error)).toBe(true);
    expect(error).toMatchObject({
      code: OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE,
      operation: "read",
    });
  });

  it("reports a durable cleanup failure instead of silently forgetting it", () => {
    const intent = getOrCreateOperatorCommandIntent(
      "pause_ad",
      "storage-remove-ad",
    );
    vi.spyOn(globalThis.localStorage, "removeItem").mockImplementation(
      () => undefined,
    );

    const error = captureError(() =>
      completeOperatorCommandIntent("pause_ad", "storage-remove-ad", intent),
    );

    expect(isOperatorCommandIntentStorageError(error)).toBe(true);
    expect(error).toMatchObject({
      code: OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE,
      operation: "remove",
    });
    expect(
      getOrCreateOperatorCommandIntent("pause_ad", "storage-remove-ad"),
    ).toBe(intent);
  });
});

function makeStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, String(value)),
  };
}

function captureError(callback: () => void): unknown {
  try {
    callback();
    return null;
  } catch (error) {
    return error;
  }
}
