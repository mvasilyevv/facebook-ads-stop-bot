/** Данные нативного канала к рабочему столу Vision. */
import type { components } from "@fb/shared/api/generated";
import { generatedApi } from "./generatedClient";

type DesktopNativeChannelResponse = components["schemas"]["DesktopNativeChannelResponse"];

/**
 * Пока канал не опубликовал ID (деплой, холодный старт) — поллим каждые 15с,
 * как и обещает экран («страница обновится сама»). Как только устройство
 * появилось, автообновление не нужно: значение стабильно до следующего цикла.
 */
export function desktopNativeRefetchInterval(
  data: DesktopNativeChannelResponse | undefined,
): number | false {
  return data?.available && data.device_id ? false : 15_000;
}

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
    {
      staleTime: 0,
      gcTime: 0,
      refetchOnMount: "always",
      refetchInterval: (query) => desktopNativeRefetchInterval(query.state.data),
    },
  );
}

/**
 * Ссылка запуска тянется ТОЛЬКО по нажатию и намеренно не кэшируется: она
 * несёт пароль канала, и держать её в состоянии открытого экрана — то же
 * самое, что отрендерить пароль в разметку, ради чего ручку и разделяли.
 */
export function useDesktopLaunchLink() {
  return generatedApi.useMutation("post", "/api/desktop/native/launch", {
    gcTime: 0,
  });
}
