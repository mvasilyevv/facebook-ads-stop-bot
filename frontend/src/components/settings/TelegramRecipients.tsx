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
import { BellRing, Copy, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/Toast";
import {
  useCreateTelegramRecipientInvite,
  useDeleteTelegramRecipient,
  useTelegramRecipientPreferences,
  useTelegramRecipients,
  useUpdateTelegramRecipientPreferences,
  type TelegramRecipient,
} from "@/lib/api/settings";

const EMPTY_DRAFT: TelegramPreferenceDraft = {
  timezone: "Europe/Kaliningrad",
  minSeverity: "warning",
  quietHoursStart: "",
  quietHoursEnd: "",
  digestLocalTime: "",
  categories: {},
  isEnabled: true,
};

export function TelegramRecipients() {
  const recipientsQuery = useTelegramRecipients();
  const createInvite = useCreateTelegramRecipientInvite();
  const deleteRecipient = useDeleteTelegramRecipient();
  const [selected, setSelected] = useState<TelegramRecipient | null>(null);
  const [removeCandidate, setRemoveCandidate] = useState<TelegramRecipient | null>(null);
  const [invite, setInvite] = useState<{
    activation_command: string;
    auth_deep_link?: string | null;
    expires_at: string;
  } | null>(null);

  async function handleCreateInvite() {
    try {
      setInvite(await createInvite.mutateAsync());
    } catch (error) {
      toast.error(
        "Не удалось создать приглашение",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  async function copyInvite() {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.activation_command);
      toast.success("Команда подключения скопирована");
    } catch {
      toast.error("Не удалось скопировать", "Выделите команду вручную");
    }
  }

  return (
    <section
      className="border-t border-[var(--color-hairline)] pt-5"
      aria-labelledby="telegram-recipients-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="telegram-recipients-heading" className="m-0 text-[16px] font-medium text-bg-11">
            Получатели
          </h3>
          <p className="m-0 mt-1 max-w-[58ch] text-[13px] leading-5 text-bg-8">
            Владелец получает действия. Остальные получатели видят только уведомления.
          </p>
        </div>
        <Button
          variant="secondary"
          leftIcon={<UserPlus size={15} aria-hidden="true" />}
          onClick={() => void handleCreateInvite()}
          loading={createInvite.isPending}
        >
          Пригласить получателя
        </Button>
      </div>

      {invite ? (
        <div className="mt-4 border-y border-[var(--color-hairline)] py-4">
          <div className="text-[14px] font-medium text-bg-11">Одноразовое подключение</div>
          <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
            Передайте команду только нужному получателю. Она действует 24 часа и не даёт права на
            действия.
          </p>
          <code className="mt-3 block break-all bg-bg-2 px-3 py-3 text-[13px] text-bg-11">
            {invite.activation_command}
          </code>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              leftIcon={<Copy size={14} />}
              onClick={() => void copyInvite()}
            >
              Скопировать команду
            </Button>
            {invite.auth_deep_link ? (
              <a
                href={invite.auth_deep_link}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-11 items-center rounded-[var(--radius-2)] px-3 text-[13px] text-accent underline underline-offset-4"
              >
                Открыть в Telegram
              </a>
            ) : null}
          </div>
        </div>
      ) : null}

      {recipientsQuery.isLoading ? (
        <div className="mt-4 space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : recipientsQuery.isError ? (
        <div role="alert" className="mt-4 border-y border-[var(--color-hairline)] py-4">
          <p className="m-0 text-[14px] text-danger">
            {safeApiProblemMessage(
              recipientsQuery.error,
              "Получатели Telegram временно недоступны",
            )}
          </p>
          <Button
            className="mt-3"
            variant="secondary"
            onClick={() => void recipientsQuery.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : recipientsQuery.data?.recipients.length ? (
        <div className="mt-4 border-y border-[var(--color-hairline)]">
          {recipientsQuery.data.recipients.map((recipient, index) => {
            const label = recipientLabel(recipient, index);
            return (
              <div
                key={recipient.id}
                className="flex min-h-14 flex-col gap-3 border-b border-[var(--color-hairline)] py-3 last:border-b-0 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="break-words text-[14px] text-bg-11">{label}</span>
                    <Badge variant="neutral" size="sm">
                      {recipient.role === "owner" ? "Владелец" : "Только уведомления"}
                    </Badge>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" size="sm" onClick={() => setSelected(recipient)}>
                    Настроить
                  </Button>
                  {recipient.role !== "owner" ? (
                    <Button variant="ghost" size="sm" onClick={() => setRemoveCandidate(recipient)}>
                      Отозвать
                    </Button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="mt-4 border-y border-[var(--color-hairline)] py-5 text-[14px] text-bg-9">
          Активных получателей пока нет.
        </div>
      )}

      <RecipientPreferencesModal recipient={selected} onClose={() => setSelected(null)} />

      <ConfirmDialog
        open={removeCandidate !== null}
        onOpenChange={(open) => {
          if (!open) setRemoveCandidate(null);
        }}
        title="Отозвать доступ к уведомлениям?"
        description={`${removeCandidate ? recipientLabel(removeCandidate, 0) : "Получатель"} больше не будет получать сообщения. История доставки сохранится.`}
        confirmLabel="Отозвать"
        onConfirm={async () => {
          if (!removeCandidate) return;
          try {
            await deleteRecipient.mutateAsync(removeCandidate.id);
            toast.success("Доступ получателя отозван");
          } catch (error) {
            toast.error(
              "Не удалось отозвать доступ",
              safeApiProblemMessage(error, "Повторите попытку"),
            );
            throw error;
          }
        }}
      />
    </section>
  );
}

function RecipientPreferencesModal({
  recipient,
  onClose,
}: {
  recipient: TelegramRecipient | null;
  onClose: () => void;
}) {
  const preferencesQuery = useTelegramRecipientPreferences(recipient?.id ?? null);
  const updatePreferences = useUpdateTelegramRecipientPreferences();
  const [draft, setDraft] = useState<TelegramPreferenceDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<TelegramPreferenceValidation>({});

  useEffect(() => {
    if (!preferencesQuery.data) return;
    setDraft(telegramPreferenceDraftFromResponse(preferencesQuery.data));
    setErrors({});
  }, [preferencesQuery.data]);

  async function handleSave() {
    if (!recipient) return;
    const nextErrors = validateTelegramPreferenceDraft(draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    try {
      await updatePreferences.mutateAsync({
        recipientId: recipient.id,
        body: telegramPreferencePayload(draft),
      });
      toast.success("Настройки получателя сохранены");
      onClose();
    } catch (error) {
      toast.error(
        "Не удалось сохранить настройки",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  return (
    <Modal
      open={recipient !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Уведомления получателя"
      description={recipient ? recipientLabel(recipient, 0) : undefined}
      size="md"
    >
      {preferencesQuery.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }, (_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      ) : preferencesQuery.isError ? (
        <div role="alert">
          <p className="m-0 text-[14px] text-danger">
            {safeApiProblemMessage(
              preferencesQuery.error,
              "Настройки получателя временно недоступны",
            )}
          </p>
          <Button
            className="mt-3"
            variant="secondary"
            onClick={() => void preferencesQuery.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <Switch
            checked={draft.isEnabled}
            onChange={() => setDraft((value) => ({ ...value, isEnabled: !value.isEnabled }))}
            label="Включить уведомления получателя"
            visualLabel="Доставка включена"
            description="Critical recovery и ошибки действий следуют серверной safety-политике."
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Часовой пояс"
              value={draft.timezone}
              errorMessage={errors.timezone}
              onChange={(event) =>
                setDraft((value) => ({ ...value, timezone: event.target.value }))
              }
              autoComplete="off"
            />
            <Select
              label="Минимальная важность"
              value={draft.minSeverity}
              options={TELEGRAM_SEVERITY_OPTIONS.map((option) => ({ ...option }))}
              onChange={(event) =>
                setDraft((value) => ({
                  ...value,
                  minSeverity: event.target.value as TelegramPreferenceDraft["minSeverity"],
                }))
              }
            />
          </div>

          <fieldset>
            <legend className="text-[12px] font-display uppercase tracking-wider text-bg-9">
              Тихие часы
            </legend>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <Input
                aria-label="Начало тихих часов"
                type="time"
                value={draft.quietHoursStart}
                errorMessage={errors.quietHours}
                onChange={(event) =>
                  setDraft((value) => ({ ...value, quietHoursStart: event.target.value }))
                }
              />
              <Input
                aria-label="Окончание тихих часов"
                type="time"
                value={draft.quietHoursEnd}
                onChange={(event) =>
                  setDraft((value) => ({ ...value, quietHoursEnd: event.target.value }))
                }
              />
            </div>
            <p className="m-0 mt-2 text-[13px] leading-5 text-bg-8">
              Critical, failed action и recovery не задерживаются тихими часами.
            </p>
          </fieldset>

          <Input
            label="Время дайджеста"
            type="time"
            value={draft.digestLocalTime}
            errorMessage={errors.digestLocalTime}
            onChange={(event) =>
              setDraft((value) => ({ ...value, digestLocalTime: event.target.value }))
            }
          />

          <fieldset>
            <legend className="text-[12px] font-display uppercase tracking-wider text-bg-9">
              Категории
            </legend>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              {TELEGRAM_CATEGORIES.map((category) => (
                <Select
                  key={category.key}
                  label={category.label}
                  value={draft.categories[category.key] ?? "inherit"}
                  options={TELEGRAM_CATEGORY_OPTIONS.map((option) => ({ ...option }))}
                  onChange={(event) =>
                    setDraft((value) => ({
                      ...value,
                      categories: {
                        ...value.categories,
                        [category.key]: event.target.value as NonNullable<
                          TelegramPreferenceDraft["categories"][string]
                        >,
                      },
                    }))
                  }
                />
              ))}
            </div>
          </fieldset>

          <ModalFooter>
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button
              variant="primary"
              leftIcon={<BellRing size={14} />}
              loading={updatePreferences.isPending}
              onClick={() => void handleSave()}
            >
              Сохранить уведомления
            </Button>
          </ModalFooter>
        </div>
      )}
    </Modal>
  );
}

function recipientLabel(recipient: TelegramRecipient, index: number): string {
  if (recipient.username) return `@${recipient.username.replace(/^@/, "")}`;
  return recipient.role === "owner" ? "Владелец Telegram" : `Получатель ${index + 1}`;
}
