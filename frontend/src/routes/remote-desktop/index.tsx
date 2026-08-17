import { createFileRoute } from "@tanstack/react-router";
import { Copy, MonitorUp, ShieldCheck } from "lucide-react";
import { HeaderSep, PageHeader } from "@/components/layout/PageHeader";
import { buttonStyles } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useDesktopLaunchLink, useDesktopNativeChannel } from "@/lib/api/desktop";
import { cn } from "@/lib/utils/cn";

/**
 * Доступ к столу — нативным приложением RustDesk через собственный брокер.
 *
 * Веб-канала больше нет: браузер на iPhone не может отдать системный буфер
 * обмена — WebKit требует свежего жеста и считает его протухшим после любого
 * await, а клиенту между жестом и буфером нужно сходить на сервер. Нативное
 * приложение этим ограничением не связано.
 *
 * Пароль канала не попадает в разметку страницы: кнопка «Открыть в приложении»
 * запрашивает готовую ссылку запуска отдельной ручкой в момент нажатия, и та
 * живёт в памяти вкладки ровно до открытия приложения.
 */

const ctaClassName = cn(buttonStyles({ variant: "primary", size: "lg" }), "min-w-44");
// Ссылка «Открыть в приложении» работает только после шага 1, поэтому она не
// главный акцент страницы: самый заметный элемент не должен быть тем, который
// у неподготовленного клиента отвечает «устройство не найдено».
const openAppClassName = cn(buttonStyles({ variant: "secondary", size: "md" }), "min-w-44");

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const { data, isPending, isError, refetch } = useDesktopNativeChannel();
  const launch = useDesktopLaunchLink();

  /**
   * Ссылку запуска забираем в момент нажатия и сразу отдаём приложению: она
   * несёт пароль канала, поэтому не должна ни попасть в разметку, ни осесть в
   * состоянии экрана. В переменной она живёт до следующей строки.
   */
  const openInApp = () => {
    launch.mutate(
      {},
      {
        onSuccess: ({ url }) => {
          window.location.assign(url);
        },
        onError: () => {
          toast.error(
            "Не удалось открыть приложение",
            "Попробуйте ещё раз или подключитесь по ID вручную.",
          );
        },
      },
    );
  };

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
            Ключ и пароль
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
                  Стол живёт на собственном брокере, а не на публичных серверах RustDesk. Пока
                  клиент не переключён на него, стол для приложения не существует.
                </p>

                <div className="mx-auto mt-7 max-w-[460px] text-left">
                  <StepHeading index={1} title="Один раз переключите клиент" />
                  <p className="mb-2.5 text-[12px] leading-5 text-bg-9">
                    В приложении: Settings → Network → ID/Relay Server. Адрес — в поля ID Server и
                    Relay Server, ключ — в поле Key.
                  </p>
                  <dl className="grid gap-2">
                    {data.server ? (
                      <ChannelRow label="Сервер (ID/Relay)" value={data.server} />
                    ) : null}
                    {data.key ? <ChannelRow label="Ключ брокера" value={data.key} /> : null}
                  </dl>
                </div>

                <div className="mx-auto mt-7 max-w-[460px] text-left">
                  <StepHeading index={2} title="Подключитесь к столу" />
                  <p className="mb-2.5 text-[12px] leading-5 text-bg-9">
                    Введите ID в приложении. Пароль канала оно запомнит после первого подключения.
                  </p>
                  <dl className="grid gap-2">
                    <ChannelRow label="ID стола" value={data.device_id} />
                  </dl>
                  <div className="mt-3 flex justify-center">
                    <button
                      type="button"
                      className={openAppClassName}
                      disabled={launch.isPending}
                      onClick={openInApp}
                    >
                      <MonitorUp size={15} aria-hidden="true" />
                      {launch.isPending ? "Открываем…" : "Открыть в приложении"}
                    </button>
                  </div>
                  <p className="mt-2 text-center text-[12px] leading-5 text-bg-8">
                    Кнопка подставляет ID и пароль — вводить ничего не нужно. Адрес и ключ она
                    передать не может, поэтому до шага 1 приложение ответит «устройство не найдено».
                  </p>
                </div>
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

/** Номер шага рядом с заголовком: порядок здесь обязателен, а не желателен. */
function StepHeading({ index, title }: { index: number; title: string }) {
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="flex size-[22px] shrink-0 items-center justify-center rounded-full border border-[var(--color-hairline-strong)] font-numeric text-[12px] text-bg-9">
        {index}
      </span>
      <h3 className="font-display text-[14px] text-bg-11">{title}</h3>
    </div>
  );
}

/** Строка значения канала: подпись, само значение и копирование в один клик. */
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
