/**
 * Persist one client idempotency key for an operator command intent.
 *
 * A rejected/ambiguous fetch must not mint a new key on the next click: the
 * server may already have committed the first request.  The key is removed
 * only after an HTTP command receipt is returned.
 */

export type OperatorCommandKind =
  | "pause_ad"
  | "activate_ad"
  | "retry_scan"
  | "abort_campaign_run"
  | "resume_campaign_run";
export type OperatorCommandIntentStorageOperation =
  | "access"
  | "read"
  | "write"
  | "remove";

const STORAGE_PREFIX = "fb-agent:operator-command-intent:v1";
export const OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE =
  "operator_command_intent_storage_unavailable";
export const OPERATOR_COMMAND_INTENT_STORAGE_USER_MESSAGE =
  "Приложению недоступно постоянное хранилище ключа защиты от повторной команды. Перезагрузите приложение; если ошибка повторится, разрешите хранение данных или используйте другой браузер.";

export class OperatorCommandIntentStorageError extends Error {
  readonly code = OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE;
  readonly operation: OperatorCommandIntentStorageOperation;
  readonly userMessage = OPERATOR_COMMAND_INTENT_STORAGE_USER_MESSAGE;

  constructor(
    operation: OperatorCommandIntentStorageOperation,
    cause?: unknown,
  ) {
    super(OPERATOR_COMMAND_INTENT_STORAGE_USER_MESSAGE, { cause });
    this.name = "OperatorCommandIntentStorageError";
    this.operation = operation;
  }
}

export function isOperatorCommandIntentStorageError(
  value: unknown,
): value is OperatorCommandIntentStorageError {
  if (value instanceof OperatorCommandIntentStorageError) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<OperatorCommandIntentStorageError>;
  return (
    candidate.code === OPERATOR_COMMAND_INTENT_STORAGE_ERROR_CODE &&
    candidate.userMessage === OPERATOR_COMMAND_INTENT_STORAGE_USER_MESSAGE &&
    ["access", "read", "write", "remove"].includes(candidate.operation ?? "")
  );
}

function scopeKey(action: OperatorCommandKind, targetId: string): string {
  return `${STORAGE_PREFIX}:${action}:${encodeURIComponent(targetId)}`;
}

function storage(): Storage {
  try {
    const candidate = globalThis.localStorage;
    if (
      !candidate ||
      typeof candidate.getItem !== "function" ||
      typeof candidate.setItem !== "function" ||
      typeof candidate.removeItem !== "function"
    ) {
      throw new Error("localStorage is unavailable");
    }
    return candidate;
  } catch (cause) {
    throwStorageError("access", cause);
  }
}

function read(durableStorage: Storage, key: string): string | null {
  try {
    return durableStorage.getItem(key);
  } catch (cause) {
    throwStorageError("read", cause);
  }
}

function write(durableStorage: Storage, key: string, value: string): void {
  try {
    durableStorage.setItem(key, value);
    if (durableStorage.getItem(key) !== value) {
      throw new Error("localStorage did not persist the command intent");
    }
  } catch (cause) {
    throwStorageError("write", cause);
  }
}

function remove(durableStorage: Storage, key: string): void {
  try {
    durableStorage.removeItem(key);
    if (durableStorage.getItem(key) !== null) {
      throw new Error(
        "localStorage did not remove the completed command intent",
      );
    }
  } catch (cause) {
    throwStorageError("remove", cause);
  }
}

function throwStorageError(
  operation: OperatorCommandIntentStorageOperation,
  cause: unknown,
): never {
  if (isOperatorCommandIntentStorageError(cause)) throw cause;
  throw new OperatorCommandIntentStorageError(operation, cause);
}

function newIntentKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getOrCreateOperatorCommandIntent(
  action: OperatorCommandKind,
  targetId: string,
): string {
  const key = scopeKey(action, targetId);
  const durableStorage = storage();
  const existing = read(durableStorage, key);
  if (existing) return existing;
  const created = newIntentKey();
  write(durableStorage, key, created);
  return created;
}

export function completeOperatorCommandIntent(
  action: OperatorCommandKind,
  targetId: string,
  idempotencyKey: string,
): void {
  const key = scopeKey(action, targetId);
  const durableStorage = storage();
  // Do not let a late response erase an explicitly newer intent.
  if (read(durableStorage, key) === idempotencyKey) remove(durableStorage, key);
}
