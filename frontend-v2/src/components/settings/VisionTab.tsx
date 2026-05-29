/**
 * VisionTab — вкладка настроек Vision anti-detect браузера:
 *   - Статус подключения.
 *   - Обновление токена (masked) и profile_id.
 *   - Кнопка reconnect (ConfirmDialog).
 */

import { useState, type ChangeEvent } from "react";
import { RefreshCcw, Eye, EyeOff } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";

import {
  useVisionSettings,
  useUpdateVision,
  useVisionReconnect,
} from "@/lib/api/settings";

export function VisionTab() {
  const [tokenInput, setTokenInput] = useState("");
  const [profileInput, setProfileInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [reconnectOpen, setReconnectOpen] = useState(false);

  const settingsQuery = useVisionSettings();
  const updateVision = useUpdateVision();
  const reconnect = useVisionReconnect();

  const settings = settingsQuery.data;

  function handleSave() {
    const payload: { vision_token?: string; profile_id?: string } = {};
    if (tokenInput.trim()) payload.vision_token = tokenInput.trim();
    if (profileInput.trim()) payload.profile_id = profileInput.trim();

    if (Object.keys(payload).length === 0) {
      toast.error("Введите хотя бы одно поле");
      return;
    }

    updateVision.mutate(payload, {
      onSuccess: () => {
        toast.success("Vision настройки сохранены");
        setShowForm(false);
        setTokenInput("");
        setProfileInput("");
      },
      onError: (err) =>
        toast.error("Ошибка сохранения", err instanceof Error ? err.message : String(err)),
    });
  }

  function handleReconnect() {
    reconnect.mutate(undefined, {
      onSuccess: () => toast.success("Vision переподключён"),
      onError: (err) =>
        toast.error("Ошибка reconnect", err instanceof Error ? err.message : String(err)),
    });
  }

  if (settingsQuery.isError) {
    return (
      <ErrorState
        title="Не удалось загрузить настройки Vision."
        error={settingsQuery.error}
        onRetry={() => settingsQuery.refetch()}
      />
    );
  }

  return (
    <>
      <ConfirmDialog
        open={reconnectOpen}
        onOpenChange={setReconnectOpen}
        title="Переподключить Vision?"
        description="Текущая сессия браузера будет закрыта и запущена заново."
        confirmWord="RECONNECT"
        confirmLabel="Переподключить"
        cancelLabel="Отмена"
        onConfirm={handleReconnect}
      />

      <div className="grid grid-cols-[1fr_320px] gap-8">
        {/* Левая колонка: форма настроек. */}
        <div className="space-y-6">
          <section className="border border-bg-5 bg-bg-1 p-5">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
              Статус Vision
            </h3>
            {settingsQuery.isLoading ? (
              <div className="space-y-3">
                <Skeleton height={18} />
                <Skeleton height={14} width="60%" />
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Badge variant={settings?.is_connected ? "success" : "neutral"}>
                    {settings?.is_connected ? "подключён" : "не подключён"}
                  </Badge>
                  <Badge variant={settings?.vision_token ? "neutral" : "warning"} size="sm">
                    {settings?.vision_token ? "токен есть" : "токен не задан"}
                  </Badge>
                </div>
                {settings?.profile_id ? (
                  <div className="text-[12px] text-bg-9">
                    Profile ID:{" "}
                    <span className="font-numeric text-bg-11">{settings.profile_id}</span>
                  </div>
                ) : (
                  <div className="text-[12px] text-bg-9">Profile ID: —</div>
                )}
              </div>
            )}
          </section>

          {/* Форма обновления токена/profile. */}
          <section>
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
              Настройка подключения
            </h3>
            {!showForm ? (
              <Button variant="secondary" size="sm" onClick={() => setShowForm(true)}>
                {settings?.vision_token ? "Обновить токен / profile" : "Настроить Vision"}
              </Button>
            ) : (
              <div className="space-y-4 max-w-sm">
                <div className="relative">
                  <Input
                    id="vision-token"
                    label="Vision X-Token"
                    type={showToken ? "text" : "password"}
                    placeholder="Введите токен Vision..."
                    value={tokenInput}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setTokenInput(e.target.value)}
                    helpText="Токен не отображается после сохранения."
                    autoComplete="off"
                    rightIcon={
                      <button
                        type="button"
                        aria-label={showToken ? "Скрыть токен" : "Показать токен"}
                        onClick={() => setShowToken((p) => !p)}
                        className="text-bg-9 hover:text-bg-11 transition-colors"
                      >
                        {showToken ? (
                          <EyeOff size={14} aria-hidden="true" />
                        ) : (
                          <Eye size={14} aria-hidden="true" />
                        )}
                      </button>
                    }
                  />
                </div>
                <Input
                  id="vision-profile"
                  label="Profile ID"
                  type="text"
                  placeholder="Например: abc123def456"
                  value={profileInput}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setProfileInput(e.target.value)}
                  helpText="UUID или slug профиля Vision."
                />
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    loading={updateVision.isPending}
                    onClick={handleSave}
                  >
                    Сохранить
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setShowForm(false);
                      setTokenInput("");
                      setProfileInput("");
                    }}
                  >
                    Отмена
                  </Button>
                </div>
              </div>
            )}
          </section>
        </div>

        {/* Правая колонка: действия. */}
        <div className="space-y-4">
          <section className="border border-bg-5 bg-bg-1 p-5 space-y-3">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-3">
              Действия
            </h3>
            <Button
              variant="secondary"
              size="sm"
              fullWidth
              leftIcon={<RefreshCcw size={13} aria-hidden="true" />}
              loading={reconnect.isPending}
              onClick={() => setReconnectOpen(true)}
            >
              Переподключить Vision
            </Button>
          </section>

          <section className="border border-bg-5 bg-bg-1 p-5">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-3">
              Справка
            </h3>
            <ul className="text-[12px] text-bg-9 space-y-1.5 list-disc list-inside">
              <li>Vision запускается на порту 3030.</li>
              <li>X-Token и Profile ID — из настроек Vision.</li>
              <li>Reconnect завершает текущую CDP-сессию.</li>
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
