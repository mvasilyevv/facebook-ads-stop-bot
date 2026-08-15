import { useEffect, useId, useMemo, useState } from "react";
import { Clock3, Monitor } from "lucide-react";
import { isOperatorDisplayTimezoneCandidate, safeApiProblemMessage } from "@fb/operator-api";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import {
  useOperatorDisplayPreference,
  useUpdateOperatorDisplayPreference,
} from "@/lib/api/settings";
import { browserTimeZone, formatDisplayDateTime } from "@/lib/timezone";

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
  const manualInputId = useId();
  const manualHintId = useId();
  const manualErrorId = useId();
  const preferenceQuery = useOperatorDisplayPreference();
  const updatePreference = useUpdateOperatorDisplayPreference();
  const deviceTimezone = useMemo(() => browserTimeZone(), []);
  const [manual, setManual] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (preferenceQuery.data?.timezone_name) {
      setManual(preferenceQuery.data.timezone_name);
    }
  }, [preferenceQuery.data?.timezone_name]);

  const effective = preferenceQuery.data?.timezone_name;
  const normalizedManual = manual.trim();
  const valid = isOperatorDisplayTimezoneCandidate(normalizedManual);
  const changed = Boolean(effective && normalizedManual !== effective);
  const preview = useMemo(() => {
    if (!effective) return null;
    return formatDisplayDateTime(new Date(), effective);
  }, [effective]);

  function save(timezoneName: string) {
    const normalized = timezoneName.trim();
    if (!isOperatorDisplayTimezoneCandidate(normalized)) return;
    setSaved(false);
    updatePreference.mutate(
      { body: { timezone_name: normalized } },
      {
        onSuccess: (preference) => {
          setManual(preference.timezone_name);
          setSaved(true);
        },
      },
    );
  }

  const queryError = preferenceQuery.isError
    ? safeApiProblemMessage(preferenceQuery.error, "Не удалось загрузить часовой пояс отображения")
    : null;
  const updateError = updatePreference.isError
    ? safeApiProblemMessage(updatePreference.error, "Не удалось сохранить часовой пояс отображения")
    : null;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card padded className="p-5">
        <div className="mb-5 flex items-start gap-3">
          <Clock3 size={18} className="mt-0.5 text-accent" aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="m-0 font-display text-[16px] font-medium text-bg-11">
              Часовой пояс отображения
            </h2>
            <p className="m-0 mt-1 max-w-[70ch] text-[14px] leading-5 text-bg-8">
              Одна настройка для web и TMA. Она меняет только подписи времени; сутки рекламного
              кабинета и серверные агрегаты не пересчитываются.
            </p>
          </div>
        </div>

        {preferenceQuery.isPending ? (
          <p role="status" className="m-0 text-[14px] text-bg-8">
            Загружаем сохранённый часовой пояс…
          </p>
        ) : queryError ? (
          <div role="alert" className="border-y border-danger/40 py-4">
            <p className="m-0 text-[14px] leading-5 text-danger">{queryError}</p>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="mt-3"
              onClick={() => void preferenceQuery.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : (
          <div className="border-y border-[var(--color-hairline)] py-4">
            <label htmlFor={manualInputId} className="block text-[14px] font-medium text-bg-11">
              Часовой пояс
            </label>
            <p id={manualHintId} className="m-0 mt-1 text-[14px] leading-5 text-bg-8">
              Например, Europe/Kaliningrad или America/New_York.
            </p>
            <input
              id={manualInputId}
              value={manual}
              list="timezone-options"
              autoComplete="off"
              spellCheck={false}
              maxLength={64}
              aria-invalid={manual.length > 0 && !valid}
              aria-describedby={
                manual.length > 0 && !valid ? `${manualHintId} ${manualErrorId}` : manualHintId
              }
              onChange={(event) => {
                setManual(event.target.value);
                setSaved(false);
              }}
              className={`mt-3 h-11 w-full rounded-[var(--radius-2)] border bg-bg-0 px-3 font-display text-[16px] text-bg-11 outline-none ${
                manual.length === 0 || valid
                  ? "border-[var(--color-hairline-strong)] focus:border-accent"
                  : "border-danger"
              }`}
            />
            <datalist id="timezone-options">
              {COMMON_TIMEZONES.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            {manual.length > 0 && !valid ? (
              <span id={manualErrorId} role="alert" className="mt-2 block text-[14px] text-danger">
                Введите часовой пояс без пробелов
              </span>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-3">
              <Button
                type="button"
                disabled={!valid || !changed || updatePreference.isPending}
                onClick={() => save(manual)}
              >
                {updatePreference.isPending ? "Сохраняем…" : "Сохранить"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={
                  updatePreference.isPending ||
                  !isOperatorDisplayTimezoneCandidate(deviceTimezone) ||
                  deviceTimezone === effective
                }
                onClick={() => save(deviceTimezone)}
              >
                Использовать {deviceTimezone}
              </Button>
            </div>

            <div className="mt-3 min-h-5" aria-live="polite">
              {saved ? (
                <span className="text-[14px] text-success">Сохранено для web и TMA.</span>
              ) : updateError ? (
                <span role="alert" className="text-[14px] text-danger">
                  {updateError}
                </span>
              ) : null}
            </div>
          </div>
        )}
      </Card>

      <Card padded className="h-fit p-5">
        <div className="flex items-center gap-2 text-bg-8">
          <Monitor size={15} aria-hidden="true" />
          <span className="font-display text-[12px] uppercase tracking-[0.08em]">Текущий вид</span>
        </div>
        <div className="mt-5 break-words font-display text-[24px] tabular-nums text-bg-11">
          {preview ?? "—"}
        </div>
        <div className="mt-2 break-all text-[14px] text-bg-8">
          {effective
            ? `Сохранённый часовой пояс: ${effective}`
            : "Часовой пояс не подтверждён сервером"}
        </div>
        <div className="mt-5 border-t border-[var(--color-hairline)] pt-4 text-[14px] leading-5 text-bg-8">
          Настройка хранится в профиле владельца. Часовой пояс устройства используется только как
          явный вариант для сохранения.
        </div>
      </Card>
    </div>
  );
}
