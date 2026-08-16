import { createFileRoute } from "@tanstack/react-router";
import { Copy, MonitorUp } from "lucide-react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { tmaApi } from "@/lib/auth";
import { haptic } from "@/lib/tg";

/**
 * Доступ к столу — нативным приложением RustDesk через собственный брокер.
 *
 * Веб-канал демонтирован: браузер на iPhone не может отдать системный буфер
 * обмена, и это ограничение WebKit, а не настройка. С телефона стол
 * открывается приложением RustDesk; здесь — всё, что нужно ввести в клиент.
 * Пароль канала сюда не попадает никогда.
 */

export const Route = createFileRoute("/desktop/")({
  component: RemoteDesktopPage,
});

function useDesktopNativeChannel() {
  return tmaApi.useQuery(
    "get",
    "/api/desktop/native",
    {},
    { staleTime: 0, gcTime: 0, refetchOnMount: "always", refetchInterval: 15_000 },
  );
}

function RemoteDesktopPage() {
  const { data, isPending, isError, refetch } = useDesktopNativeChannel();

  return (
    <div className="flex min-h-full flex-col pb-6">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="СИСТЕМА · УДАЛЁННЫЙ ДОСТУП"
        title="Рабочий стол"
      />

      <div className="flex flex-col gap-5 p-4">
        <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1">
          <div className="flex min-h-[156px] flex-col items-center justify-center gap-3 px-5 py-6 text-center">
            <span className="flex size-14 items-center justify-center rounded-[var(--radius-3)] bg-bg-3 text-accent">
              <MonitorUp size={28} strokeWidth={1.5} aria-hidden="true" />
            </span>
            <div>
              <h2 className="font-display text-[20px] font-medium text-bg-11">
                Подключение к рабочему столу
              </h2>
              <p className="mt-1 text-[12px] leading-relaxed text-bg-9">
                Через приложение RustDesk. Адрес и ключ вводятся один раз, пароль канала приложение
                запомнит после первого подключения.
              </p>
            </div>
          </div>
        </section>

        <section>
          <Eyebrow className="mb-2.5 flex">ПОДКЛЮЧЕНИЕ</Eyebrow>
          {isPending ? (
            <p role="status" className="text-center text-[13px] text-bg-9">
              Загружаем данные канала…
            </p>
          ) : isError ? (
            <>
              <p className="mb-3 text-center text-[12px] text-danger" role="alert">
                Данные канала недоступны. Повторите запрос.
              </p>
              <Button fullWidth size="lg" onClick={() => void refetch()}>
                Повторить
              </Button>
            </>
          ) : data?.available && data.device_id ? (
            <>
              <a
                href={`rustdesk://${data.device_id}`}
                className="mb-4 flex min-h-12 w-full items-center justify-center gap-2 rounded-[var(--radius-2)] bg-accent font-display text-[15px] font-semibold text-bg-0"
                onClick={() => haptic.impact("medium")}
              >
                <MonitorUp size={17} strokeWidth={1.7} aria-hidden="true" />
                Открыть в приложении
              </a>
              <dl className="grid gap-2">
                <ChannelRow label="ID стола" value={data.device_id} />
                {data.server ? <ChannelRow label="Сервер (ID/Relay)" value={data.server} /> : null}
                {data.key ? <ChannelRow label="Ключ брокера" value={data.key} /> : null}
              </dl>
            </>
          ) : (
            <p role="status" className="text-center text-[13px] leading-relaxed text-bg-9">
              Стол ещё не опубликовал ID канала: после деплоя это занимает меньше минуты. Экран
              обновится сам.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

/** Значение канала с копированием: на телефоне ввод руками — худший путь. */
function ChannelRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 px-3 py-2">
      <div className="min-w-0">
        <dt className="text-[12px] uppercase tracking-[0.07em] text-bg-8">{label}</dt>
        <dd className="m-0 truncate font-numeric text-[13px] text-bg-11">{value}</dd>
      </div>
      <button
        type="button"
        aria-label={`Скопировать: ${label}`}
        className="flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline)] text-bg-9"
        onClick={() => {
          void navigator.clipboard
            .writeText(value)
            .then(() => haptic.notify("success"))
            .catch(() => haptic.notify("error"));
        }}
      >
        <Copy size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
