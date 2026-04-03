import { useEffect, useState } from 'react';
import { ObserverSettingsSection } from '../components/settings/ObserverSettingsSection.jsx';
import { SettingsToast } from '../components/settings/SettingsToast.jsx';
import { TelegramSettingsSection } from '../components/settings/TelegramSettingsSection.jsx';
import { VisionSettingsSection } from '../components/settings/VisionSettingsSection.jsx';
import { useIsMobile } from '../hooks/useIsMobile.js';
import { useSettingsData } from '../hooks/useSettingsData.js';

export default function SettingsPage() {
  const isMobile = useIsMobile();
  const [browserOpen, setBrowserOpen] = useState(() => !isMobile);
  const settings = useSettingsData();

  useEffect(() => {
    if (!isMobile) setBrowserOpen(true);
  }, [isMobile]);

  if (settings.loading) {
    return (
      <div className="space-y-md animate-fade-in">
        <div>
          <h1 className="text-lg text-primary">Настройки</h1>
          <p className="text-sm text-muted">Загружаем конфигурацию…</p>
        </div>
        <div className="flex items-center gap-3 py-12 text-sm text-muted">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Загрузка настроек...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-lg animate-fade-in">
      <div>
        <h1 className="text-lg text-primary">Настройки</h1>
        <p className="text-sm text-muted">Observer, пороги, Telegram и Vision.</p>
      </div>

      <ObserverSettingsSection
        observer={settings.observer.value}
        onChange={settings.observer.setValue}
        onSave={settings.observer.save}
        saving={settings.saving}
      />

      <TelegramSettingsSection
        telegram={settings.telegram.value}
        newToken={settings.telegram.newToken}
        onNewTokenChange={settings.telegram.setNewToken}
        authChecking={settings.telegram.authChecking}
        currentBotUsername={settings.telegram.currentBotUsername}
        pollerStatusMeta={settings.telegram.pollerStatusMeta}
        primaryRecipient={settings.telegram.primaryRecipient}
        recipients={settings.telegram.recipients}
        inviteCode={settings.telegram.inviteCode}
        inviteDeepLink={settings.telegram.inviteDeepLink}
        pendingAuthCommand={settings.telegram.pendingAuthCommand}
        pendingAuthDeepLink={settings.telegram.pendingAuthDeepLink}
        isWaitingTelegramAuth={settings.telegram.isWaitingTelegramAuth}
        onConnectTelegram={settings.telegram.connect}
        onRevokeTelegram={settings.telegram.revoke}
        onRefreshStatus={() => settings.telegram.checkAuthStatus(false)}
        onDeleteRecipient={settings.telegram.deleteRecipient}
        onCreateInvite={settings.telegram.createInvite}
        onClearInvite={settings.telegram.clearInvite}
        onOpenTelegram={settings.telegram.openTelegram}
        onCopyWithToast={settings.telegram.copyWithToast}
        saving={settings.saving}
      />

      <VisionSettingsSection
        vision={settings.vision.value}
        visionProfiles={settings.vision.visionProfiles}
        showVisionToken={settings.vision.showVisionToken}
        onToggleTokenVisibility={() => settings.vision.setShowVisionToken((v) => !v)}
        onVisionChange={settings.vision.setValue}
        onLoadProfiles={settings.vision.loadProfiles}
        profilesLoading={settings.vision.profilesLoading}
        onSave={settings.vision.save}
        onReconnect={settings.vision.reconnect}
        saving={settings.saving}
        browserOpen={browserOpen}
        onToggleBrowserOpen={() => setBrowserOpen((v) => !v)}
      />

      {settings.toast && (
        <SettingsToast
          message={settings.toast.message}
          type={settings.toast.type}
          onClose={() => settings.setToast(null)}
        />
      )}
    </div>
  );
}
