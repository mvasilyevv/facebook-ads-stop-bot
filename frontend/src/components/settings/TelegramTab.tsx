/**
 * TelegramTab — настройки Telegram-бота:
 * токен, статус авторизации, deep-link, web-app-url.
 */

import { useState, type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useCreateTelegramOwnerInvite,
  useTelegramSettings,
  useUpdateTelegramToken,
  useDeleteTelegramToken,
} from "@/lib/api/settings";
import { CheckCircle2, Copy, ExternalLink, KeyRound, XCircle } from "lucide-react";

export const TelegramTab: FC = () => {
  const { data, isLoading, error, refetch } = useTelegramSettings();
  const inviteMut = useCreateTelegramOwnerInvite();
  const tokenMut = useUpdateTelegramToken();
  const deleteMut = useDeleteTelegramToken();

  const [newToken, setNewToken] = useState("");

  if (isLoading) {
    return (
      <div className="space-y-3 max-w-xl">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  const handleSaveToken = async () => {
    if (!newToken.trim()) return;
    try {
      await tokenMut.mutateAsync(newToken.trim());
      setNewToken("");
      toast.success("Токен сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения токена", e instanceof Error ? e.message : String(e));
    }
  };

  const handleDeleteToken = async () => {
    try {
      await deleteMut.mutateAsync();
      toast.success("Токен удалён");
    } catch (e) {
      toast.error("Ошибка удаления токена", e instanceof Error ? e.message : String(e));
    }
  };

  const handleCreateOwnerInvite = async () => {
    try {
      await inviteMut.mutateAsync();
      toast.success("Ссылка подключения создана");
    } catch (e) {
      toast.error("Не удалось создать ссылку", e instanceof Error ? e.message : String(e));
    }
  };

  const handleCopyCommand = async () => {
    if (!data?.activation_command) return;
    try {
      await navigator.clipboard.writeText(data.activation_command);
      toast.success("Команда скопирована");
    } catch (e) {
      toast.error("Не удалось скопировать", e instanceof Error ? e.message : String(e));
    }
  };

  const isAuthorized = data?.is_authorized ?? false;
  const pollerOnline = data?.poller_status === "ONLINE";
  const hasOwnerInvite = Boolean(data?.activation_command);
  const inviteExpiresAt = data?.auth_invite_expires_at
    ? new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(data.auth_invite_expires_at))
    : null;

  return (
    <div className="space-y-5 max-w-xl">
      {/* Статус */}
      <Card eyebrow="Статус бота" padded>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Авторизация</span>
            {isAuthorized ? (
              <Badge variant="success" size="sm">
                <CheckCircle2 size={10} aria-hidden="true" />
                Авторизован
              </Badge>
            ) : (
              <Badge variant="neutral" size="sm">
                <XCircle size={10} aria-hidden="true" />
                Не авторизован
              </Badge>
            )}
          </div>

          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Poller</span>
            <Badge variant={pollerOnline ? "success" : "neutral"} size="sm">
              {data?.poller_status ?? "OFFLINE"}
            </Badge>
          </div>

          {data?.bot_username && (
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-bg-10">Бот</span>
              <span className="font-display text-[12px] text-bg-9">@{data.bot_username}</span>
            </div>
          )}
        </div>

        {/* Одноразовая owner-ссылка */}
        {isAuthorized && (
          <div className="mt-4 pt-4 border-t border-[var(--hairline)]">
            <div className="flex items-center gap-2 text-[11px] text-bg-8 uppercase tracking-wider mb-2">
              <KeyRound size={12} aria-hidden="true" />
              Подключение владельца
            </div>

            {hasOwnerInvite ? (
              <div className="space-y-3">
                {data?.auth_deep_link && (
                  <a
                    href={data.auth_deep_link}
                    target="_blank"
                    rel="noreferrer"
                    className="block font-display text-[12px] leading-relaxed text-accent break-all underline decoration-accent/35 underline-offset-4 hover:decoration-accent transition-colors"
                  >
                    {data.auth_deep_link}
                  </a>
                )}
                <div className="rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 py-2.5">
                  <div className="text-[10px] uppercase tracking-[0.14em] text-bg-7 mb-1">
                    Команда
                  </div>
                  <code className="font-display text-[13px] text-bg-12 break-all">
                    {data?.activation_command}
                  </code>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {data?.auth_deep_link && (
                    <a
                      href={data.auth_deep_link}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-h-9 items-center gap-2 rounded-[var(--radius-2)] bg-accent px-3 text-[12px] font-medium text-bg-0 transition-opacity hover:opacity-90"
                    >
                      Открыть в Telegram
                      <ExternalLink size={13} aria-hidden="true" />
                    </a>
                  )}
                  <Button variant="secondary" onClick={() => void handleCopyCommand()}>
                    <Copy size={13} aria-hidden="true" />
                    Скопировать команду
                  </Button>
                </div>
                {inviteExpiresAt && (
                  <p className="text-[11px] text-bg-8">
                    Одноразовая ссылка действует до {inviteExpiresAt} и исчезнет после подключения.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-[12px] leading-relaxed text-bg-9">
                  Создайте одноразовую owner-ссылку. Код будет сразу встроен и в ссылку, и в команду{" "}
                  <code className="text-bg-11">/start</code>.
                </p>
                <Button
                  variant="primary"
                  onClick={() => void handleCreateOwnerInvite()}
                  loading={inviteMut.isPending}
                >
                  <KeyRound size={14} aria-hidden="true" />
                  Сгенерировать ссылку
                </Button>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Токен */}
      <Card eyebrow="Токен бота" padded>
        <Input
          id="tg-token"
          label="Bot Token"
          placeholder="1234567890:ABC..."
          type="password"
          value={newToken}
          onChange={(e) => setNewToken(e.target.value)}
          helpText="Telegram Bot API токен от @BotFather. Хранится зашифрованным."
        />
        <div className="mt-4 flex gap-3">
          <Button
            variant="primary"
            onClick={() => void handleSaveToken()}
            loading={tokenMut.isPending}
            disabled={!newToken.trim()}
          >
            Сохранить токен
          </Button>
          {isAuthorized && (
            <Button
              variant="danger"
              onClick={() => void handleDeleteToken()}
              loading={deleteMut.isPending}
            >
              Удалить токен
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
};
