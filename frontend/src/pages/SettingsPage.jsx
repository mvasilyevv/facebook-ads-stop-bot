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
    if (!isMobile) {
      setBrowserOpen(true);
    }
  }, [isMobile]);

  if (settings.loading) {
    return (
      <div className="settings-page animate-in">
        <div className="page-header">
          <div>
            <h1 className="page-title">Настройки</h1>
            <div className="page-subtitle">Загружаем конфигурацию…</div>
          </div>
        </div>
        <div className="loading-state">
          <div className="spinner" />
          <div>Загрузка настроек...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-page animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Настройки</h1>
          <div className="page-subtitle">Observer, пороги, Telegram и Vision.</div>
        </div>
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
        onToggleTokenVisibility={() =>
          settings.vision.setShowVisionToken((value) => !value)
        }
        onVisionChange={settings.vision.setValue}
        onLoadProfiles={settings.vision.loadProfiles}
        profilesLoading={settings.vision.profilesLoading}
        onSave={settings.vision.save}
        onReconnect={settings.vision.reconnect}
        saving={settings.saving}
        browserOpen={browserOpen}
        onToggleBrowserOpen={() => setBrowserOpen((value) => !value)}
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
