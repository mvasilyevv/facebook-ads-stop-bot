import { useEffect, useState } from "react";
import {
  TELEGRAM_CATEGORIES,
  TELEGRAM_CATEGORY_OPTIONS,
  TELEGRAM_SEVERITY_OPTIONS,
  telegramPreferenceDraftFromResponse,
  telegramPreferencePayload,
  validateTelegramPreferenceDraft,
  type TelegramPreferenceDraft,
  type TelegramPreferenceValidation,
} from "@fb/features/settings";
import { safeApiProblemMessage } from "@fb/operator-api";
import { ArrowLeft } from "lucide-react";

import {
  Button,
  EmptyState,
  Input,
  Select,
  Skeleton,
  Switch,
} from "@/components/ui";
import {
  useTelegramRecipientPreferences,
  useUpdateTelegramRecipientPreferences,
  type TelegramRecipient,
} from "@/lib/api";
import { haptic } from "@/lib/tg";

const EMPTY_DRAFT: TelegramPreferenceDraft = {
  timezone: "Europe/Kaliningrad",
  minSeverity: "warning",
  quietHoursStart: "",
  quietHoursEnd: "",
  digestLocalTime: "",
  categories: {},
  isEnabled: true,
};

export function TelegramRecipientPreferences({
  recipient,
  label,
  canEdit,
  onBack,
}: {
  recipient: TelegramRecipient;
  label: string;
  canEdit: boolean;
  onBack: () => void;
}) {
  const preferencesQuery = useTelegramRecipientPreferences(recipient.id);
  const updatePreferences = useUpdateTelegramRecipientPreferences();
  const [draft, setDraft] = useState<TelegramPreferenceDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<TelegramPreferenceValidation>({});
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!preferencesQuery.data) return;
    setDraft(telegramPreferenceDraftFromResponse(preferencesQuery.data));
    setErrors({});
  }, [preferencesQuery.data]);

  async function handleSave() {
    if (!canEdit) return;
    const nextErrors = validateTelegramPreferenceDraft(draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    try {
      await updatePreferences.mutateAsync({
        recipientId: recipient.id,
        body: telegramPreferencePayload(draft),
      });
      haptic.notify("success");
      onBack();
    } catch (error) {
      haptic.notify("error");
      setProblem(
        safeApiProblemMessage(error, "Настройки получателя не сохранены"),
      );
    }
  }

  return (
    <div className="space-y-5 pb-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex min-h-11 items-center gap-2 text-[14px] text-bg-10"
      >
        <ArrowLeft size={16} aria-hidden="true" />
        Все получатели
      </button>

      <div>
        <h3 className="m-0 break-words text-[16px] font-medium text-bg-11">
          {label}
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          {recipient.role === "owner"
            ? "Владелец может получать action-карточки."
            : "Получатель видит уведомления без action-кнопок."}
        </p>
      </div>

      {preferencesQuery.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }, (_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      ) : preferencesQuery.isError ? (
        <EmptyState
          title="Настройки недоступны"
          description={safeApiProblemMessage(
            preferencesQuery.error,
            "Не удалось получить настройки получателя",
          )}
          action={{
            label: "Повторить",
            onClick: () => void preferencesQuery.refetch(),
          }}
        />
      ) : (
        <>
          {problem ? (
            <p
              role="alert"
              className="m-0 border-y border-danger/40 py-3 text-[14px] text-danger"
            >
              {problem}
            </p>
          ) : null}

          <div className="border-y border-[var(--color-hairline)]">
            <Switch
              label="Доставка уведомлений"
              checked={draft.isEnabled}
              disabled={!canEdit}
              onChange={(event) =>
                setDraft((value) => ({
                  ...value,
                  isEnabled: event.target.checked,
                }))
              }
            />
          </div>

          <Input
            label="Timezone"
            value={draft.timezone}
            disabled={!canEdit}
            errorMessage={errors.timezone}
            onChange={(event) =>
              setDraft((value) => ({ ...value, timezone: event.target.value }))
            }
            autoComplete="off"
            spellCheck={false}
          />

          <Select
            label="Минимальная важность"
            value={draft.minSeverity}
            disabled={!canEdit}
            options={TELEGRAM_SEVERITY_OPTIONS.map((option) => ({ ...option }))}
            onChange={(event) =>
              setDraft((value) => ({
                ...value,
                minSeverity: event.target
                  .value as TelegramPreferenceDraft["minSeverity"],
              }))
            }
          />

          <fieldset>
            <legend className="text-[12px] uppercase tracking-[0.07em] text-bg-9">
              Тихие часы
            </legend>
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Input
                aria-label="Начало тихих часов"
                type="time"
                value={draft.quietHoursStart}
                disabled={!canEdit}
                errorMessage={errors.quietHours}
                onChange={(event) =>
                  setDraft((value) => ({
                    ...value,
                    quietHoursStart: event.target.value,
                  }))
                }
              />
              <Input
                aria-label="Окончание тихих часов"
                type="time"
                value={draft.quietHoursEnd}
                disabled={!canEdit}
                onChange={(event) =>
                  setDraft((value) => ({
                    ...value,
                    quietHoursEnd: event.target.value,
                  }))
                }
              />
            </div>
            <p className="m-0 mt-2 text-[13px] leading-5 text-bg-8">
              Critical, failed action и recovery не задерживаются.
            </p>
          </fieldset>

          <Input
            label="Время дайджеста"
            type="time"
            value={draft.digestLocalTime}
            disabled={!canEdit}
            errorMessage={errors.digestLocalTime}
            onChange={(event) =>
              setDraft((value) => ({
                ...value,
                digestLocalTime: event.target.value,
              }))
            }
          />

          <fieldset>
            <legend className="text-[12px] uppercase tracking-[0.07em] text-bg-9">
              Категории
            </legend>
            <div className="mt-2 space-y-3">
              {TELEGRAM_CATEGORIES.map((category) => (
                <Select
                  key={category.key}
                  label={category.label}
                  value={draft.categories[category.key] ?? "inherit"}
                  disabled={!canEdit}
                  options={TELEGRAM_CATEGORY_OPTIONS.map((option) => ({
                    ...option,
                  }))}
                  onChange={(event) =>
                    setDraft((value) => ({
                      ...value,
                      categories: {
                        ...value.categories,
                        [category.key]: event.target
                          .value as TelegramPreferenceDraft["categories"][string],
                      },
                    }))
                  }
                />
              ))}
            </div>
          </fieldset>

          <Button
            fullWidth
            disabled={!canEdit}
            loading={updatePreferences.isPending}
            onClick={() => void handleSave()}
          >
            Сохранить уведомления
          </Button>
        </>
      )}
    </div>
  );
}
