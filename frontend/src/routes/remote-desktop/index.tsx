import { createFileRoute } from "@tanstack/react-router";
import { Copy, MonitorUp, ShieldCheck } from "lucide-react";
import { HeaderSep, PageHeader } from "@/components/layout/PageHeader";
import { buttonStyles } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useDesktopNativeChannel } from "@/lib/api/desktop";
import { cn } from "@/lib/utils/cn";

/**
 * Доступ к столу — нативным приложением RustDesk через собственный брокер.
 *
 * Веб-канала больше нет: браузер на iPhone не может отдать системный буфер
 * обмена — WebKit требует свежего жеста и считает его протухшим после любого
 * await, а клиенту между жестом и буфером нужно сходить на сервер. Нативное
 * приложение этим ограничением не связано.
 *
 * Пароль канала на страницу не попадает никогда: его задаёт владелец при
 * деплое, приложение запоминает его после первого подключения.
 */

const ctaClassName = cn(buttonStyles({ variant: "primary", size: "lg" }), "min-w-44");

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const { data, isPending, isError, refetch } = useDesktopNativeChannel();

  return (
    <>
      <PageHeader
        title="Рабочий стол"
        subtitle={
          <>
            Vision Server
            <HeaderSep />
            нативный канал через собственный брокер
          </>
        }
      />

      <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1">
        <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-hairline)] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-3 text-accent">
              <MonitorUp size={17} strokeWidth={1.6} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="font-display text-[13px] text-bg-11">Vision Server</p>
              <p className="truncate text-[12px] text-bg-8">Единый защищённый контур</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 font-display text-[12px] uppercase tracking-[0.08em] text-success">
            <ShieldCheck size={14} strokeWidth={1.7} aria-hidden="true" />
            Приватная сеть
          </div>
        </div>

        <div className="flex min-h-[360px] items-center justify-center px-5 py-10 sm:min-h-[420px] sm:px-10 sm:py-12">
          <div className="w-full max-w-[560px] text-center">
            <span className="mx-auto flex size-14 items-center justify-center rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-2 text-accent">
              <MonitorUp size={27} strokeWidth={1.45} aria-hidden="true" />
            </span>
            <h2 className="mt-5 font-display text-[20px] font-medium text-bg-11 sm:text-[22px]">
              Подключение к рабочему столу
            </h2>

            {isPending ? (
              <div role="status" aria-label="Загрузка данных канала" className="mt-6 grid gap-3">
                <Skeleton className="mx-auto h-11 w-56" />
                <Skeleton className="mx-auto h-24 w-full" />
              </div>
            ) : isError ? (
              <>
                <p
                  role="alert"
                  className="mx-auto mt-2 max-w-[450px] text-[13px] leading-relaxed text-danger"
                >
                  Данные канала недоступны. Повторите запрос.
                </p>
                <div className="mt-6 flex justify-center">
                  <button type="button" className={ctaClassName} onClick={() => void refetch()}>
                    Повторить
                  </button>
                </div>
              </>
            ) : data?.available && data.device_id ? (
              <>
                <p className="mx-auto mt-2 max-w-[460px] text-[13px] leading-relaxed text-bg-9">
                  Откройте приложение RustDesk или подключитесь по ID вручную. Пароль канала
                  приложение запомнит после первого подключения.
                </p>
                <div className="mt-6 flex justify-center">
                  <a href={`rustdesk://${data.device_id}`} className={ctaClassName}>
                    <MonitorUp size={15} aria-hidden="true" />
                    Открыть в приложении
                  </a>
                </div>
                <dl className="mx-auto mt-7 grid max-w-[460px] gap-2 text-left">
                  <ChannelRow label="ID стола" value={data.device_id} />
                  {data.server ? <ChannelRow label="Сервер (ID/Relay)" value={data.server} /> : null}
                  {data.key ? <ChannelRow label="Ключ брокера" value={data.key} /> : null}
                </dl>
                <p className="mx-auto mt-4 max-w-[460px] text-[12px] leading-5 text-bg-8">
                  Первая настройка клиента: Settings → Network → ID/Relay Server — адрес сервера и
                  ключ выше. Сервер доступен только из приватной сети.
                </p>
              </>
            ) : (
              <>
                <p className="mx-auto mt-2 max-w-[450px] text-[13px] leading-relaxed text-bg-9">
                  Стол ещё не опубликовал ID канала: после деплоя это занимает меньше минуты.
                  Страница обновится сама.
                </p>
                {data?.server ? (
                  <dl className="mx-auto mt-6 grid max-w-[460px] gap-2 text-left">
                    <ChannelRow label="Сервер (ID/Relay)" value={data.server} />
                    {data.key ? <ChannelRow label="Ключ брокера" value={data.key} /> : null}
                  </dl>
                ) : null}
              </>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

/** Строка значения канала: подпись, само значение и копирование в один клик. */
function ChannelRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 px-3 py-2">
      <div className="min-w-0">
        <dt className="text-[12px] uppercase tracking-[0.07em] text-bg-8">{label}</dt>
        <dd className="m-0 truncate font-numeric text-[13px] text-bg-11">{value}</dd>
      </div>
      <button
        type="button"
        aria-label={`Скопировать: ${label}`}
        className="flex size-9 shrink-0 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline)] text-bg-9 hover:text-bg-11"
        onClick={() => {
          void navigator.clipboard
            .writeText(value)
            .then(() => toast.success(`${label} — скопировано`))
            .catch(() => toast.error("Не удалось скопировать"));
        }}
      >
        <Copy size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
