import { useMemo, useState } from "react";
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
            <p className="m-0 mt-1 text-[11px] leading-5 text-bg-8">
              Меняет подписи времени в UI и heatmap. Сутки рекламного кабинета и серверные агрегаты
              не пересчитываются.
            </p>
          </div>
        </div>

        <label className="mb-3 flex cursor-pointer items-start gap-3 rounded-[var(--radius-2)] border border-[var(--hairline)] p-4 hover:bg-bg-2">
          <input
            type="radio"
            checked={configured === "auto"}
            onChange={() => setConfigured("auto")}
            className="mt-0.5"
          />
          <span>
            <strong className="block text-[12px] font-medium text-bg-11">Автоматически</strong>
            <span className="mt-1 block text-[10px] text-bg-8">
              Timezone ноутбука: {browserTimeZone()}
            </span>
          </span>
        </label>

        <label className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-2)] border border-[var(--hairline)] p-4 hover:bg-bg-2">
          <input
            type="radio"
            checked={configured !== "auto"}
            onChange={() => valid && setConfigured(manual)}
            className="mt-0.5"
          />
          <span className="min-w-0 flex-1">
            <strong className="block text-[12px] font-medium text-bg-11">
              Выбрать IANA timezone
            </strong>
            <input
              value={manual}
              list="timezone-options"
              onChange={(event) => setManual(event.target.value)}
              onBlur={() => valid && configured !== "auto" && setConfigured(manual)}
              className={`mt-2 h-9 w-full rounded-[var(--radius-2)] border bg-bg-0 px-3 font-display text-[11px] text-bg-11 outline-none ${valid ? "border-[var(--hairline-strong)] focus:border-accent" : "border-danger"}`}
            />
            <datalist id="timezone-options">
              {COMMON_TIMEZONES.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            {!valid ? (
              <span className="mt-1 block text-[10px] text-danger">Неизвестный IANA timezone</span>
            ) : null}
            {valid && configured !== "auto" && configured !== manual ? (
              <button
                type="button"
                onClick={() => setConfigured(manual)}
                className="mt-2 text-[11px] text-accent hover:underline"
              >
                Применить
              </button>
            ) : null}
          </span>
        </label>
      </Card>

      <Card padded className="h-fit p-5">
        <div className="flex items-center gap-2 text-bg-8">
          <Monitor size={15} />
          <span className="font-display text-[10px] uppercase tracking-[0.08em]">Текущий вид</span>
        </div>
        <div className="mt-5 font-display text-[24px] tabular-nums text-bg-11">{preview}</div>
        <div className="mt-2 text-[10px] text-bg-7">Эффективный timezone: {effective}</div>
        <div className="mt-5 border-t border-[var(--hairline)] pt-4 text-[10px] leading-4 text-bg-8">
          Настройка хранится только в этом браузере и не влияет на других операторов.
        </div>
      </Card>
    </div>
  );
}
