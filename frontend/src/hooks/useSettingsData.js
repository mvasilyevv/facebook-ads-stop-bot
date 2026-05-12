import { useCallback, useEffect, useState } from 'react';
import {
  applyBrowserColumnWidths,
  createInviteCode,
  deleteTelegramRecipient,
  getObserverSettings,
  getTelegramRecipients,
  getTelegramSettings,
  getVisionProfiles,
  getVisionSettings,
  revokeTelegram,
  saveBrowserColumnWidths,
  setTelegramToken,
  setTelegramWebAppUrl,
  updateObserverSettings,
  updateVisionSettings,
  visionReconnect,
} from '../api.js';
import {
  copyTextToClipboard,
  getTelegramPollerStatusMeta,
  makeTelegramDeepLink,
  normalizeBotUsername,
} from '../components/settings/settingsUtils.js';
import { useAsyncPolling } from './useAsyncPolling.js';
import { useRefreshOnResume } from './useRefreshOnResume.js';

const DEFAULT_OBSERVER = {
  is_scanning_enabled: true,
  warning_percent_of_stop: 80,
  stop_percent_of_base: 80,
  cpc_warning_percent_of_stop: 80,
  cpc_stop_percent_of_base: 80,
  cpl_warning_percent_of_stop: 80,
  cpl_stop_percent_of_base: 80,
  cpr_warning_percent_of_stop: 80,
  cpr_stop_percent_of_base: 80,
  auto_enable_recommendations: false,
};

const DEFAULT_TELEGRAM = {
  bot_token: '',
  chat_id: '',
  is_authorized: false,
  bot_username: '',
  auth_code: '',
  poller_status: 'OFFLINE',
  last_poller_heartbeat_at: null,
  auth_deep_link: '',
  activation_command: '',
  primary_recipient: null,
  active_invite: null,
};

const DEFAULT_VISION = {
  api_url: 'http://127.0.0.1:3030',
  x_token: '',
  profile_id: '',
  has_token: false,
  auto_restart_on_missing_cdp: true,
  runtime_status: 'NOT_CONFIGURED',
  runtime_status_message: 'Vision ещё не настроен',
  profile_running: false,
  cdp_port: null,
  cdp_ready: false,
  column_widths_saved_count: 0,
};

function mergeObserverState(data) {
  return {
    is_scanning_enabled:
      data.is_scanning_enabled ?? DEFAULT_OBSERVER.is_scanning_enabled,
    warning_percent_of_stop:
      data.warning_percent_of_stop ?? DEFAULT_OBSERVER.warning_percent_of_stop,
    stop_percent_of_base: data.stop_percent_of_base ?? DEFAULT_OBSERVER.stop_percent_of_base,
    cpc_warning_percent_of_stop:
      data.cpc_warning_percent_of_stop ??
      data.warning_percent_of_stop ??
      DEFAULT_OBSERVER.cpc_warning_percent_of_stop,
    cpc_stop_percent_of_base:
      data.cpc_stop_percent_of_base ??
      data.stop_percent_of_base ??
      DEFAULT_OBSERVER.cpc_stop_percent_of_base,
    cpl_warning_percent_of_stop:
      data.cpl_warning_percent_of_stop ??
      data.warning_percent_of_stop ??
      DEFAULT_OBSERVER.cpl_warning_percent_of_stop,
    cpl_stop_percent_of_base:
      data.cpl_stop_percent_of_base ??
      data.stop_percent_of_base ??
      DEFAULT_OBSERVER.cpl_stop_percent_of_base,
    cpr_warning_percent_of_stop:
      data.cpr_warning_percent_of_stop ??
      data.warning_percent_of_stop ??
      DEFAULT_OBSERVER.cpr_warning_percent_of_stop,
    cpr_stop_percent_of_base:
      data.cpr_stop_percent_of_base ??
      data.stop_percent_of_base ??
      DEFAULT_OBSERVER.cpr_stop_percent_of_base,
    auto_enable_recommendations:
      data.auto_enable_recommendations ?? DEFAULT_OBSERVER.auto_enable_recommendations,
  };
}

function mergeTelegramState(data) {
  return {
    bot_token: data.bot_token || '',
    chat_id: data.chat_id || '',
    is_authorized: data.is_authorized || false,
    bot_username: data.bot_username || '',
    auth_code: data.auth_code || '',
    poller_status: data.poller_status || 'OFFLINE',
    last_poller_heartbeat_at: data.last_poller_heartbeat_at || null,
    auth_deep_link: data.auth_deep_link || '',
    activation_command: data.activation_command || '',
    web_app_url: data.web_app_url || '',
    primary_recipient: data.primary_recipient || null,
    active_invite: data.active_invite || null,
  };
}

function mergeVisionState(data) {
  return {
    api_url: data.api_url || DEFAULT_VISION.api_url,
    x_token: '',
    profile_id: data.profile_id || '',
    has_token: data.has_token || false,
    auto_restart_on_missing_cdp: data.auto_restart_on_missing_cdp ?? true,
    runtime_status: data.runtime_status || DEFAULT_VISION.runtime_status,
    runtime_status_message: data.runtime_status_message || DEFAULT_VISION.runtime_status_message,
    profile_running: data.profile_running || false,
    cdp_port: data.cdp_port ?? null,
    cdp_ready: data.cdp_ready || false,
    column_widths_saved_count:
      data.column_widths_saved_count ?? DEFAULT_VISION.column_widths_saved_count,
  };
}

export function useSettingsData() {
  const [observer, setObserver] = useState(DEFAULT_OBSERVER);
  const [telegram, setTelegram] = useState(DEFAULT_TELEGRAM);
  const [newToken, setNewToken] = useState('');
  const [authResult, setAuthResult] = useState(null);
  const [recipients, setRecipients] = useState([]);
  const [inviteCode, setInviteCode] = useState(null);
  const [authChecking, setAuthChecking] = useState(false);
  const [vision, setVision] = useState(DEFAULT_VISION);
  const [visionProfiles, setVisionProfiles] = useState([]);
  const [showVisionToken, setShowVisionToken] = useState(false);
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState('');
  const [toast, setToast] = useState(null);

  const currentBotUsername = normalizeBotUsername(
    telegram.bot_username || authResult?.bot_username || inviteCode?.bot_username,
  );
  const pollerStatusMeta = getTelegramPollerStatusMeta(telegram.poller_status);
  const primaryRecipient = telegram.primary_recipient;
  const pendingAuthCode = authResult?.auth_code || telegram.auth_code || '';
  const pendingAuthCommand =
    authResult?.activation_command ||
    telegram.activation_command ||
    (pendingAuthCode ? `/start ${pendingAuthCode}` : '');
  const pendingAuthDeepLink =
    authResult?.auth_deep_link ||
    telegram.auth_deep_link ||
    makeTelegramDeepLink(currentBotUsername, pendingAuthCode);
  const inviteDeepLink =
    inviteCode?.deep_link ||
    makeTelegramDeepLink(inviteCode?.bot_username || currentBotUsername, inviteCode?.code);
  const isWaitingTelegramAuth = !telegram.is_authorized && Boolean(pendingAuthCode);

  const loadObserverSettings = useCallback(
    async (silent = false) => {
      try {
        const data = await getObserverSettings();
        if (data && typeof data === 'object') {
          setObserver(mergeObserverState(data));
        }
        return data;
      } catch (err) {
        if (!silent) {
          setToast({ message: err.message || 'Не удалось загрузить настройки Observer', type: 'error' });
        }
        return null;
      }
    },
    [],
  );

  const loadTelegramSettings = useCallback(
    async (silent = false) => {
      try {
        const data = await getTelegramSettings();
        if (data && typeof data === 'object') {
          const nextTelegram = mergeTelegramState(data);
          setTelegram((prev) => ({ ...prev, ...nextTelegram }));
          setInviteCode(data.active_invite || null);
          if (!nextTelegram.is_authorized) {
            setRecipients([]);
          }
        }
        return data;
      } catch (err) {
        if (!silent) {
          setToast({ message: err.message || 'Не удалось загрузить настройки Telegram', type: 'error' });
        }
        return null;
      }
    },
    [],
  );

  const loadRecipients = useCallback(
    async (silent = false) => {
      try {
        const data = await getTelegramRecipients();
        setRecipients(Array.isArray(data) ? data : []);
        return data;
      } catch (err) {
        if (!silent) {
          setToast({ message: err.message || 'Не удалось загрузить получателей', type: 'error' });
        }
        return null;
      }
    },
    [],
  );

  const loadVisionSettings = useCallback(
    async (silent = false) => {
      try {
        const data = await getVisionSettings();
        if (data && typeof data === 'object') {
          setVision(mergeVisionState(data));
        }
        return data;
      } catch (err) {
        if (!silent) {
          setToast({ message: err.message || 'Не удалось загрузить настройки Vision', type: 'error' });
        }
        return null;
      }
    },
    [],
  );

  const refreshAllSettings = useCallback(async ({ showLoading = false } = {}) => {
    if (showLoading) {
      setLoading(true);
    }
    try {
      const [, telegramData] = await Promise.all([
        loadObserverSettings(true),
        loadTelegramSettings(true),
        loadVisionSettings(true),
      ]);
      if (telegramData?.is_authorized) {
        await loadRecipients(true);
      }
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, [
    loadObserverSettings,
    loadTelegramSettings,
    loadVisionSettings,
    loadRecipients,
  ]);

  useEffect(() => {
    void refreshAllSettings({ showLoading: true });
  }, [refreshAllSettings]);

  useEffect(() => {
    if (telegram.is_authorized) {
      void loadRecipients(true);
    }
  }, [loadRecipients, telegram.is_authorized]);

  const copyWithToast = useCallback(async (value, successText) => {
    try {
      await copyTextToClipboard(value);
      setToast({ message: successText, type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Не удалось скопировать', type: 'error' });
    }
  }, []);

  const saveObserver = useCallback(async () => {
    if (saving === 'observer') return;
    setSaving('observer');
    try {
      await updateObserverSettings({
        is_scanning_enabled: observer.is_scanning_enabled,
        cpc_warning_percent_of_stop: observer.cpc_warning_percent_of_stop,
        cpc_stop_percent_of_base: observer.cpc_stop_percent_of_base,
        cpl_warning_percent_of_stop: observer.cpl_warning_percent_of_stop,
        cpl_stop_percent_of_base: observer.cpl_stop_percent_of_base,
        cpr_warning_percent_of_stop: observer.cpr_warning_percent_of_stop,
        cpr_stop_percent_of_base: observer.cpr_stop_percent_of_base,
      });
      setToast({ message: 'Настройки Observer сохранены', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  }, [observer, saving]);

  const connectTelegram = useCallback(async () => {
    if (!newToken.trim()) return;
    setSaving('telegram');
    try {
      const result = await setTelegramToken(newToken.trim());
      const botUsername = normalizeBotUsername(result.bot_username || '');
      const authCode = result.auth_code || '';
      setAuthResult({
        ...result,
        bot_username: botUsername,
        auth_deep_link: result.auth_deep_link || '',
        activation_command:
          result.activation_command || (authCode ? `/start ${authCode}` : ''),
      });
      setNewToken('');
      setToast({
        message: 'Токен проверен. Отправьте код боту или откройте ссылку.',
        type: 'success',
      });
      const telegramData = await loadTelegramSettings(true);
      if (telegramData?.is_authorized) {
        await loadRecipients(true);
      }
    } catch (err) {
      setToast({ message: err.message || 'Не удалось проверить токен', type: 'error' });
    } finally {
      setSaving('');
    }
  }, [loadRecipients, loadTelegramSettings, newToken]);

  const revokeTelegramSettings = useCallback(async () => {
    if (!confirm('Отключить Telegram и очистить привязку? Уведомления перестанут приходить.')) {
      return;
    }
    setSaving('telegram');
    try {
      await revokeTelegram();
      setTelegram(DEFAULT_TELEGRAM);
      setAuthResult(null);
      setInviteCode(null);
      setRecipients([]);
      setNewToken('');
      setToast({ message: 'Telegram отключён', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    } finally {
      setSaving('');
    }
  }, []);

  const saveWebAppUrl = useCallback(async (url) => {
    setSaving('telegram-webapp');
    try {
      const result = await setTelegramWebAppUrl(url);
      setTelegram((prev) => ({ ...prev, web_app_url: result.web_app_url ?? url }));
      setToast({ message: 'Web App URL сохранён', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    } finally {
      setSaving('');
    }
  }, []);

  const checkAuthStatus = useCallback(
    async (silent = false) => {
      setAuthChecking(true);
      try {
        const tgData = await loadTelegramSettings(silent);
        if (tgData?.is_authorized) {
          setAuthResult(null);
          if (!silent) {
            setToast({ message: 'Telegram подключён и готов принимать уведомления', type: 'success' });
          }
          await loadRecipients(true);
          return true;
        }
        if (!silent) {
          setToast({
            message: 'Подтверждение ещё не пришло. Отправьте /start и подождите пару секунд.',
            type: 'info',
          });
        }
        return false;
      } catch (err) {
        if (!silent) {
          setToast({ message: err.message || 'Не удалось проверить статус', type: 'error' });
        }
        return false;
      } finally {
        setAuthChecking(false);
      }
    },
    [loadRecipients, loadTelegramSettings],
  );

  const deleteRecipient = useCallback(
    async (id) => {
      if (!confirm('Удалить дополнительного получателя и отключить ему уведомления?')) return;
      try {
        await deleteTelegramRecipient(id);
        setRecipients((prev) => prev.filter((recipient) => recipient.id !== id));
        setToast({ message: 'Получатель удалён', type: 'success' });
      } catch (err) {
        setToast({ message: err.message || 'Ошибка', type: 'error' });
      }
    },
    [],
  );

  const createInvite = useCallback(async () => {
    setSaving('invite');
    try {
      const result = await createInviteCode();
      const botUsername = normalizeBotUsername(result.bot_username || currentBotUsername);
      setInviteCode({
        ...result,
        bot_username: botUsername,
        deep_link: result.deep_link || '',
        role: result.role || 'recipient',
      });
      setToast({ message: 'Инвайт-код создан', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка генерации кода', type: 'error' });
    } finally {
      setSaving('');
    }
  }, [currentBotUsername]);

  const saveVision = useCallback(async () => {
    if (saving === 'vision') return;
    setSaving('vision');
    try {
      await updateVisionSettings({
        api_url: vision.api_url,
        x_token: vision.x_token,
        profile_id: '',
      });
      setToast({ message: 'Vision настройки сохранены', type: 'success' });
      const visionData = await getVisionSettings();
      if (visionData && typeof visionData === 'object') {
        setVision(mergeVisionState(visionData));
      }
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  }, [saving, vision]);

  const visionReconnectAction = useCallback(async () => {
    setSaving('reconnect');
    try {
      const response = await visionReconnect();
      setToast({
        message: response?.message || 'Профиль Vision перезапущен. Observer переподключится автоматически.',
        type: 'success',
      });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    } finally {
      setSaving('');
    }
  }, []);

  const applyColumnWidthsAction = useCallback(async () => {
    setSaving('column-widths');
    try {
      const response = await applyBrowserColumnWidths();
      if (!response?.applied) {
        const missing = Array.isArray(response?.missing_columns)
          ? response.missing_columns.join(', ')
          : '';
        setToast({
          message: missing
            ? `Не удалось применить автоширину. Нет колонок: ${missing}`
            : response?.error_message || 'Не удалось применить автоширину колонок',
          type: 'error',
        });
        return;
      }
      const matchedCount = Array.isArray(response.matched_columns)
        ? response.matched_columns.length
        : 0;
      const adjustedCount = response.adjusted_cells || 0;
      setToast({
        message: response.used_saved_widths
          ? `Сохранённые ширины применены: обработано ${matchedCount}, изменено ${adjustedCount}`
          : `Автоширина колонок применена: обработано ${matchedCount}, изменено ${adjustedCount}`,
        type: 'success',
      });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка автоширины колонок', type: 'error' });
    } finally {
      setSaving('');
    }
  }, []);

  const saveColumnWidthsAction = useCallback(async () => {
    setSaving('save-column-widths');
    try {
      const response = await saveBrowserColumnWidths();
      if (!response?.saved) {
        setToast({
          message: response?.error_message || 'Не удалось сохранить ширины колонок',
          type: 'error',
        });
        return;
      }
      setVision((current) => ({
        ...current,
        column_widths_saved_count: response.saved_count || 0,
      }));
      setToast({
        message: `Слепок ширины колонок сохранён (${response.saved_count || 0} колонок)`,
        type: 'success',
      });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения ширины колонок', type: 'error' });
    } finally {
      setSaving('');
    }
  }, []);

  const loadProfiles = useCallback(async () => {
    setProfilesLoading(true);
    try {
      const data = await getVisionProfiles();
      setVisionProfiles(Array.isArray(data) ? data : []);
      if (!data?.length) setToast({ message: 'Профили не найдены', type: 'info' });
    } catch (err) {
      setToast({ message: err.message || 'Не удалось загрузить профили', type: 'error' });
    } finally {
      setProfilesLoading(false);
    }
  }, []);

  const handleOpenTelegram = useCallback((link) => {
    if (!link) return;
    window.open(link, '_blank', 'noopener,noreferrer');
  }, []);

  useAsyncPolling(
    async () => {
      await checkAuthStatus(true);
    },
    {
      enabled: isWaitingTelegramAuth,
      intervalMs: 2500,
      runImmediately: true,
    },
  );

  useRefreshOnResume(() => {
    void refreshAllSettings();
  }, !loading);

  const clearInvite = useCallback(() => {
    setInviteCode(null);
  }, []);

  return {
    loading,
    saving,
    toast,
    setToast,
    observer: {
      value: observer,
      setValue: setObserver,
      save: saveObserver,
    },
    telegram: {
      value: telegram,
      setValue: setTelegram,
      newToken,
      setNewToken,
      authResult,
      setAuthResult,
      recipients,
      inviteCode,
      setInviteCode,
      authChecking,
      currentBotUsername,
      pollerStatusMeta,
      primaryRecipient,
      pendingAuthCode,
      pendingAuthCommand,
      pendingAuthDeepLink,
      inviteDeepLink,
      isWaitingTelegramAuth,
      copyWithToast,
      connect: connectTelegram,
      revoke: revokeTelegramSettings,
      checkAuthStatus,
      deleteRecipient,
      createInvite,
      clearInvite,
      openTelegram: handleOpenTelegram,
      saveWebAppUrl,
    },
    vision: {
      value: vision,
      setValue: setVision,
      visionProfiles,
      showVisionToken,
      setShowVisionToken,
      profilesLoading,
      save: saveVision,
      reconnect: visionReconnectAction,
      saveColumnWidths: saveColumnWidthsAction,
      applyColumnWidths: applyColumnWidthsAction,
      loadProfiles,
    },
  };
}
