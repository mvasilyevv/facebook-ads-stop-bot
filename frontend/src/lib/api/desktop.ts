/** Данные нативного канала к рабочему столу Vision. */
import { generatedApi } from "./generatedClient";

/**
 * Канал живёт на стороне стола: ID выдаёт брокер после старта, и кэшировать
 * снимок незачем — оператор открывает страницу, чтобы увидеть текущее
 * состояние, а не вчерашнее.
 */
export function useDesktopNativeChannel() {
  return generatedApi.useQuery(
    "get",
    "/api/desktop/native",
    {},
    { staleTime: 0, gcTime: 0, refetchOnMount: "always", refetchInterval: 15_000 },
  );
}
