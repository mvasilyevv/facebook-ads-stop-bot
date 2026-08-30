import { useEffect, useMemo, useState } from "react";
import {
  isOperatorDisplayTimezoneCandidate,
  safeApiProblemMessage,
} from "@fb/operator-api";
import { formatZonedDateTime } from "@fb/shared/format/time";

import { Button, Input } from "@/components/ui";
import {
  useOperatorDisplayPreference,
  useUpdateOperatorDisplayPreference,
} from "@/lib/settingsApi";
import { useTelegramMainButton } from "@/lib/useTelegramMainButton";

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

function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function DisplaySettings({ canEdit }: { canEdit: boolean }) {
  const preferenceQuery = useOperatorDisplayPreference(canEdit);
  const updatePreference = useUpdateOperatorDisplayPreference();
  const deviceTimezone = useMemo(() => browserTimeZone(), []);
  const [manual, setManual] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (preferenceQuery.data?.timezone_name) {
      setManual(preferenceQuery.data.timezone_name);
    }
  }, [preferenceQuery.data?.timezone_name]);

  // Считаем здесь (а не после ранних return) — нужно хуку MainButton ниже,
  // а хуки обязаны вызываться в одном порядке на каждый рендер.
  const effective = preferenceQuery.data?.timezone_name;
  const normalizedManual = manual.trim();
  const valid = isOperatorDisplayTimezoneCandidate(normalizedManual);

  // save объявлена как function-декларация (хойстится) — ссылаться на неё
  // до её текстового определения ниже безопасно.
  const mainButton = useTelegramMainButton({
    text: updatePreference.isPending ? "Сохраняем…" : "Сохранить",
    onClick: () => save(manual),
    visible: canEdit && !preferenceQuery.isPending && !preferenceQuery.isError,
    disabled:
      !valid || normalizedManual === effective || updatePreference.isPending,
    loading: updatePreference.isPending,
  });

  if (!canEdit) return <OwnerOnlyNotice />;

  const inputError =
    manual.length > 0 && !valid
      ? "Введите IANA timezone без пробелов"
      : clientError;
  const preview = effective ? formatZonedDateTime(new Date(), effective) : null;

  function save(timezoneName: string) {
    const normalized = timezoneName.trim();
    if (!isOperatorDisplayTimezoneCandidate(normalized)) {
      setClientError("Введите IANA timezone без пробелов");
      return;
    }
    setClientError(null);
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

  if (preferenceQuery.isPending) {
    return (
      <p role="status" className="m-0 py-4 text-[14px] text-bg-8">
        Загружаем сохранённый timezone…
      </p>
    );
  }

  if (preferenceQuery.isError) {
    return (
      <div role="alert" className="border-y border-danger/40 py-4">
        <p className="m-0 text-[14px] leading-5 text-danger">
          {safeApiProblemMessage(
            preferenceQuery.error,
            "Не удалось загрузить timezone отображения",
          )}
        </p>
        <Button
          className="mt-3"
          variant="secondary"
          fullWidth
          onClick={() => void preferenceQuery.refetch()}
        >
          Повторить
        </Button>
      </div>
    );
  }

  const updateError = updatePreference.isError
    ? safeApiProblemMessage(
        updatePreference.error,
        "Не удалось сохранить timezone отображения",
      )
    : null;

  return (
    <div className="space-y-5 pb-4">
      <div className="border-y border-[var(--color-hairline)] py-4">
        <p className="m-0 mb-4 text-[14px] leading-5 text-bg-8">
          Одна серверная настройка для web и TMA. Границы суток рекламного
          кабинета она не меняет.
        </p>
        <Input
          label="IANA timezone"
          value={manual}
          list="mini-timezone-options"
          errorMessage={inputError ?? undefined}
          onChange={(event) => {
            setManual(event.target.value);
            setClientError(null);
            setSaved(false);
          }}
          autoComplete="off"
          spellCheck={false}
          maxLength={64}
        />
        <datalist id="mini-timezone-options">
          {COMMON_TIMEZONES.map((timezone) => (
            <option key={timezone} value={timezone} />
          ))}
        </datalist>
        {!mainButton.available ? (
          <Button
            className="mt-3"
            variant="primary"
            fullWidth
            disabled={
              !valid ||
              normalizedManual === effective ||
              updatePreference.isPending
            }
            onClick={() => save(manual)}
          >
            {updatePreference.isPending ? "Сохраняем…" : "Сохранить"}
          </Button>
        ) : null}
        <Button
          className="mt-3"
          variant="secondary"
          fullWidth
          disabled={
            updatePreference.isPending ||
            !isOperatorDisplayTimezoneCandidate(deviceTimezone) ||
            deviceTimezone === effective
          }
          onClick={() => save(deviceTimezone)}
        >
          Использовать {deviceTimezone}
        </Button>
        <div className="mt-3 min-h-5" aria-live="polite">
          {saved ? (
            <span className="text-[14px] text-success">
              Сохранено для web и TMA.
            </span>
          ) : updateError ? (
            <span role="alert" className="text-[14px] text-danger">
              {updateError}
            </span>
          ) : null}
        </div>
      </div>

      <div className="border-y border-[var(--color-hairline)] py-4">
        <div className="text-[14px] text-bg-8">Текущий вид</div>
        <div className="mt-1 break-words font-display text-[22px] tabular-nums text-bg-11">
          {preview ?? "—"}
        </div>
        <p className="m-0 mt-2 break-all text-[14px] leading-5 text-bg-8">
          {effective
            ? `${effective}. Подтверждено серверным профилем владельца.`
            : "Timezone не подтверждён сервером."}
        </p>
      </div>
    </div>
  );
}

function OwnerOnlyNotice() {
  return (
    <p
      role="status"
      className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] leading-5 text-warning"
    >
      Настройка профиля доступна только владельцу. Получатели уведомлений не
      могут её читать или менять.
    </p>
  );
}
