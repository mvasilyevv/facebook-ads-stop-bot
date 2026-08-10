import {
  createContext,
  useContext,
  type PropsWithChildren,
} from "react";

import type { OperatorRealtimeStatus } from "./realtime";

const OperatorRealtimeContext =
  createContext<OperatorRealtimeStatus>("reconnecting");

export function OperatorRealtimeStatusProvider({
  status,
  children,
}: PropsWithChildren<{ status: OperatorRealtimeStatus }>) {
  return (
    <OperatorRealtimeContext.Provider value={status}>
      {children}
    </OperatorRealtimeContext.Provider>
  );
}

/** Money actions fail closed unless the current socket has reconciled a snapshot. */
export function useOperatorRealtimeStatus(): OperatorRealtimeStatus {
  return useContext(OperatorRealtimeContext);
}
