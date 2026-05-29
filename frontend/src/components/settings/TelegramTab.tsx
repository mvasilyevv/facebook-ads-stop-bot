/**
 * TelegramTab — вкладка настроек Telegram:
 *   - Статус авторизации, bot_username, poller_status.
 *   - Установка токена (masked input).
 *   - Список recipients с кнопкой удаления (ConfirmDialog).
 *   - Генерация invite-кода.
 *   - Auth deep-link (copy).
 */

import { useState, type ChangeEvent } from "react";
import { Copy, Link2, Trash2, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { formatRelativeTime } from "@/lib/utils/format";
import type { TelegramRecipient } from "@/lib/types/api";

import {
  useTelegramSettings,
  useTelegramRecipients,
  useSetTelegramToken,
  useDeleteTelegramToken,
  useDeleteTelegramRecipient,
  useCreateTelegramInvite,
} from "@/lib/api/settings";

export function TelegramTab() {
  // Состояние формы токена (masked).
  const [tokenInput, setTokenInput] = useState("");
  const [showTokenForm, setShowTokenForm] = useState(false);
  // id получателя для удаления.
  const [deleteTarget, setDeleteTarget] = useState<TelegramRecipient | null>(null);
  // Сгенерированный invite code.
  const [inviteCode, setInviteCode] = useState<string | null>(null);

  const settingsQuery = useTelegramSettings();
  const recipientsQuery = useTelegramRecipients();

  const setToken = useSetTelegramToken();
  const deleteToken = useDeleteTelegramToken();
  const deleteRecipient = useDeleteTelegramRecipient();
  const createInvite = useCreateTelegramInvite();

  const settings = settingsQuery.data;

  /** Скопировать текст в буфер обмена. */
  async function copyToClipboard(text: string, label = "Скопировано") {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(label);
    } catch {
      toast.error("Не удалось скопировать");
    }
  }

  function handleSetToken() {
    if (!tokenInput.trim()) {
      toast.error("Введите токен");
      return;
    }
    // Не логируем токен — только индикатор.
    setToken.mutate(tokenInput.trim(), {
      onSuccess: () => {
        toast.success("Токен сохранён");
        setTokenInput("");
        setShowTokenForm(false);
      },
      onError: (err) =>
        toast.error("Ошибка сохранения токена", err instanceof Error ? err.message : String(err)),
    });
  }

  function handleDeleteToken() {
    deleteToken.mutate(undefined, {
      onSuccess: () => toast.success("Токен удалён"),
      onError: (err) =>
        toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
    });
  }

  function handleDeleteRecipient() {
    if (!deleteTarget) return;
    deleteRecipient.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success("Получатель удалён");
        setDeleteTarget(null);
      },
      onError: (err) => {
        toast.error("Ошибка", err instanceof Error ? err.message : String(err));
        setDeleteTarget(null);
      },
    });
  }

  function handleGenerateInvite() {
    createInvite.mutate(undefined, {
      onSuccess: (data) => {
        setInviteCode(data.code); // backend возвращает code, не invite_code
        toast.success("Invite-код создан");
      },
      onError: (err) =>
        toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
    });
  }

  if (settingsQuery.isError) {
    return (
      <ErrorState
        title="Не удалось загрузить настройки Telegram."
        error={settingsQuery.error}
        onRetry={() => settingsQuery.refetch()}
      />
    );
  }

  return (
    <>
      {/* ConfirmDialog для удаления получателя. */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}
        title="Удалить получателя?"
        description={`Пользователь ${deleteTarget?.username ?? deleteTarget?.chat_id} потеряет доступ к алертам.`}
        confirmWord="DELETE"
        confirmLabel="Удалить"
        cancelLabel="Отмена"
        onConfirm={handleDeleteRecipient}
      />

      <div className="grid grid-cols-[1fr_320px] gap-8">
        {/* Левая колонка. */}
        <div className="space-y-6">
          {/* Блок: статус авторизации. */}
          <section className="border border-bg-5 bg-bg-1 p-5">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
              Статус бота
            </h3>
            {settingsQuery.isLoading ? (
              <div className="space-y-3">
                <Skeleton height={18} />
                <Skeleton height={14} width="60%" />
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <Badge variant={settings?.is_authorized ? "success" : "neutral"}>
                    {settings?.is_authorized ? "авторизован" : "не авторизован"}
                  </Badge>
                  <Badge variant="neutral" withDot={false}>
                    poller: {settings?.poller_status ?? "—"}
                  </Badge>
                </div>
                {settings?.bot_username ? (
                  <div className="text-[12px] text-bg-9">
                    Бот:{" "}
                    <span className="text-bg-11 font-numeric">@{settings.bot_username}</span>
                  </div>
                ) : null}
              </div>
            )}
          </section>

          {/* Блок: токен. */}
          <section>
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
              Токен бота
            </h3>
            {!showTokenForm ? (
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowTokenForm(true)}
                >
                  {settings?.is_authorized ? "Заменить токен" : "Установить токен"}
                </Button>
                {settings?.is_authorized ? (
                  <Button
                    variant="danger"
                    size="sm"
                    loading={deleteToken.isPending}
                    onClick={handleDeleteToken}
                  >
                    Удалить токен
                  </Button>
                ) : null}
              </div>
            ) : (
              <div className="flex items-end gap-2">
                <Input
                  id="tg-token"
                  label="Bot Token"
                  type="password"
                  placeholder="1234567890:ABCDef..."
                  value={tokenInput}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setTokenInput(e.target.value)}
                  helpText="Токен не логируется и не отображается."
                  className="max-w-sm"
                  autoComplete="off"
                />
                <Button
                  variant="primary"
                  size="sm"
                  loading={setToken.isPending}
                  onClick={handleSetToken}
                >
                  Сохранить
                </Button>
                <Button variant="ghost" size="sm" onClick={() => { setShowTokenForm(false); setTokenInput(""); }}>
                  Отмена
                </Button>
              </div>
            )}
          </section>

          {/* Блок: список recipients. */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
                Получатели алертов
              </h3>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<UserPlus size={13} aria-hidden="true" />}
                loading={createInvite.isPending}
                onClick={handleGenerateInvite}
              >
                Пригласить
              </Button>
            </div>

            {/* Invite code отображается после генерации. */}
            {inviteCode ? (
              <div className="mb-4 border border-bg-5 bg-bg-1 p-4 flex items-center justify-between gap-4">
                <div>
                  <div className="text-[10px] font-display uppercase tracking-widest text-bg-9 mb-1">
                    Invite code
                  </div>
                  <div className="font-numeric text-[14px] text-accent">{inviteCode}</div>
                  <div className="text-[11px] text-bg-9 mt-1">
                    Перешлите пользователю или скопируйте ссылку.
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Copy size={13} aria-hidden="true" />}
                  aria-label="Скопировать invite code"
                  onClick={() => copyToClipboard(inviteCode, "Invite code скопирован")}
                >
                  Скопировать
                </Button>
              </div>
            ) : null}

            {recipientsQuery.isError ? (
              <ErrorState
                title="Не удалось загрузить получателей."
                error={recipientsQuery.error}
                onRetry={() => recipientsQuery.refetch()}
              />
            ) : recipientsQuery.isLoading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} height={40} />
                ))}
              </div>
            ) : recipientsQuery.data?.length === 0 ? (
              <p className="text-[13px] text-bg-9 py-4">
                Получателей нет. Пригласите первого пользователя.
              </p>
            ) : (
              <div className="border border-bg-5 divide-y divide-bg-5">
                {recipientsQuery.data?.map((r) => (
                  <RecipientRow
                    key={r.id}
                    recipient={r}
                    onDelete={() => setDeleteTarget(r)}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Правая колонка: auth deep-link. */}
        <div className="space-y-6">
          {settings?.auth_deep_link ? (
            <section className="border border-bg-5 bg-bg-1 p-5">
              <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
                Auth deep-link
              </h3>
              <p className="text-[12px] text-bg-9 mb-3">
                Ссылка для авторизации бота через Telegram.
              </p>
              <Button
                variant="secondary"
                size="sm"
                fullWidth
                leftIcon={<Link2 size={13} aria-hidden="true" />}
                onClick={() => copyToClipboard(settings.auth_deep_link!, "Deep-link скопирован")}
              >
                Скопировать deep-link
              </Button>
            </section>
          ) : null}

          <section className="border border-bg-5 bg-bg-1 p-5">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-3">
              Как подключить
            </h3>
            <ol className="text-[12px] text-bg-9 space-y-2 list-decimal list-inside">
              <li>Создайте бота через @BotFather, скопируйте токен.</li>
              <li>Вставьте токен в поле выше.</li>
              <li>Нажмите «Пригласить» и отправьте код через /start в боте.</li>
            </ol>
          </section>
        </div>
      </div>
    </>
  );
}

/** Строка одного Telegram-получателя. */
function RecipientRow({
  recipient,
  onDelete,
}: {
  recipient: TelegramRecipient;
  onDelete: () => void;
}) {
  const isRevoked = !!recipient.revoked_at;

  return (
    <div className="flex items-center justify-between px-4 py-3 hover:bg-bg-2 transition-colors">
      <div>
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-bg-11 font-medium">
            {recipient.username ? `@${recipient.username}` : `chat:${recipient.chat_id}`}
          </span>
          <Badge variant={isRevoked ? "disabled" : "neutral"} size="sm" withDot={false}>
            {recipient.role}
          </Badge>
          {isRevoked && (
            <Badge variant="disabled" size="sm">отозван</Badge>
          )}
        </div>
        <div className="text-[11px] text-bg-9 mt-0.5">
          Добавлен {formatRelativeTime(recipient.created_at)}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        aria-label={`Удалить ${recipient.username ?? recipient.chat_id}`}
        onClick={onDelete}
        className="text-danger hover:bg-danger-bg"
      >
        <Trash2 size={14} aria-hidden="true" />
      </Button>
    </div>
  );
}
