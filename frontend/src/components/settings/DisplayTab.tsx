import { useId, useMemo, useState } from "react";
import { Clock3, Monitor } from "lucide-react";

import { Card } from "@/components/ui/Card";
import { browserTimeZone, isValidTimeZone, resolveDisplayTimeZone } from "@/lib/timezone";
import { useUiStore } from "@/stores/ui";

const COMMON_TIMEZONES = [
  "Europe/Kaliningrad",
  "Europe/Moscow",
  "Europe/Warsaw",
  "Europe/London",
  "America/New_York",
  "America/Los_Angeles",
  "Asia/Dubai",
  "Asia/Bangkok",
  "UTC",
];

export function DisplayTab() {
  const autoRadioId = useId();
  const manualRadioId = useId();
  const manualInputId = useId();
  const manualErrorId = useId();
  const configured = useUiStore((state) => state.displayTimeZone);
  const setConfigured = useUiStore((state) => state.setDisplayTimeZone);
  const [manual, setManual] = useState(configured === "auto" ? browserTimeZone() : configured);
  const effective = resolveDisplayTimeZone(configured);
  const valid = isValidTimeZone(manual);
  const preview = useMemo(
    () =>
      new Intl.DateTimeFormat("ru-RU", {
        timeZone: effective,
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(new Date()),
    [effective],
  );

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card padded className="p-5">
        <div className="mb-5 flex items-start gap-3">
          <Clock3 size={18} className="mt-0.5 text-accent" />
          <div>
            <h2 className="m-0 font-display text-[13px] font-medium text-bg-11">
              Часовой пояс отображения
            </h2>
            <p className="m-0 mt-1 text-[12px] leading-5 text-bg-8">
              Меняет подписи времени в UI и heatmap. Сутки рекламного кабинета и серверные агрегаты
              не пересчитываются.
            </p>
          </div>
        </div>

        <fieldset>
          <legend className="sr-only">Режим часового пояса отображения</legend>

          <label
            htmlFor={autoRadioId}
            className="mb-3 flex cursor-pointer items-start gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] p-4 hover:bg-bg-2"
          >
            <input
              id={autoRadioId}
              name="display-timezone-mode"
              type="radio"
              checked={configured === "auto"}
              onChange={() => setConfigured("auto")}
              className="mt-0.5"
            />
            <span>
              <strong className="block text-[12px] font-medium text-bg-11">Автоматически</strong>
              <span className="mt-1 block text-[12px] text-bg-8">
                Timezone ноутбука: {browserTimeZone()}
              </span>
            </span>
          </label>

          <div className="flex items-start gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] p-4 hover:bg-bg-2">
            <input
              id={manualRadioId}
              name="display-timezone-mode"
              type="radio"
              checked={configured !== "auto"}
              onChange={() => valid && setConfigured(manual)}
              className="mt-0.5"
            />
            <div className="min-w-0 flex-1">
              <label
                htmlFor={manualRadioId}
                className="block cursor-pointer text-[12px] font-medium text-bg-11"
              >
                Выбрать IANA timezone
              </label>
              <label htmlFor={manualInputId} className="sr-only">
                Название IANA timezone
              </label>
              <input
                id={manualInputId}
                value={manual}
                list="timezone-options"
                aria-invalid={!valid}
                aria-describedby={!valid ? manualErrorId : undefined}
                onChange={(event) => setManual(event.target.value)}
                onBlur={() => valid && configured !== "auto" && setConfigured(manual)}
                className={`mt-2 h-11 w-full rounded-[var(--radius-2)] border bg-bg-0 px-3 font-display text-[12px] text-bg-11 outline-none ${valid ? "border-[var(--color-hairline-strong)] focus:border-accent" : "border-danger"}`}
              />
              <datalist id="timezone-options">
                {COMMON_TIMEZONES.map((zone) => (
                  <option key={zone} value={zone} />
                ))}
              </datalist>
              {!valid ? (
                <span id={manualErrorId} className="mt-1 block text-[12px] text-danger">
                  Неизвестный IANA timezone
                </span>
              ) : null}
              {valid && configured !== "auto" && configured !== manual ? (
                <button
                  type="button"
                  onClick={() => setConfigured(manual)}
                  className="mt-2 inline-flex min-h-11 items-center px-2 text-[12px] text-accent hover:underline"
                >
                  Применить
                </button>
              ) : null}
            </div>
          </div>
        </fieldset>
      </Card>

      <Card padded className="h-fit p-5">
        <div className="flex items-center gap-2 text-bg-8">
          <Monitor size={15} />
          <span className="font-display text-[12px] uppercase tracking-[0.08em]">Текущий вид</span>
        </div>
        <div className="mt-5 font-display text-[24px] tabular-nums text-bg-11">{preview}</div>
        <div className="mt-2 text-[12px] text-bg-8">Эффективный timezone: {effective}</div>
        <div className="mt-5 border-t border-[var(--color-hairline)] pt-4 text-[12px] leading-4 text-bg-8">
          Настройка хранится только в этом браузере и не влияет на других операторов.
        </div>
      </Card>
    </div>
  );
}
