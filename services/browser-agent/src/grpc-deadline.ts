/** Bind a server-streaming operation to the absolute gRPC deadline. */

export function remainingGrpcDeadlineMs(call: any): number | undefined {
  const raw = call.getDeadline?.();
  const deadlineMs = raw instanceof Date ? raw.getTime() : Number(raw);
  if (!Number.isFinite(deadlineMs)) return undefined;
  return Math.max(0, deadlineMs - Date.now());
}

export function bindGrpcDeadlineAbort(
  call: any,
  controller: AbortController,
): () => void {
  const remainingMs = remainingGrpcDeadlineMs(call);
  if (remainingMs === undefined) return () => undefined;
  if (remainingMs <= 0) {
    controller.abort('grpc_deadline_exceeded');
    return () => undefined;
  }
  const timer = setTimeout(
    () => controller.abort('grpc_deadline_exceeded'),
    Math.max(1, Math.floor(remainingMs)),
  );
  timer.unref?.();
  return () => clearTimeout(timer);
}
