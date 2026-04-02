import { useCallback, useEffect, useState } from 'react';
import {
  createInviteCode,
  deleteTelegramRecipient,
  getObserverSettings,
  getTelegramRecipients,
  getTelegramSettings,
  getVisionProfiles,
  getVisionSettings,
  revokeTelegram,
  setTelegramToken,
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
  interval_seconds: 90,
  jitter_seconds: 10,
  is_scanning_enabled: true,
  warning_percent_of_stop: 80,
  stop_percent_of_base: 100,
  cpc_warning_percent_of_stop: 80,
  cpc_stop_percent_of_base: 100,
  cpl_warning_percent_of_stop: 80,
  cpl_stop_percent_of_base: 100,
  cpr_warning_percent_of_stop: 80,
  cpr_stop_percent_of_base: 100,
};

const DEFAULT_TELEGRAM = {
  bot_token: '',
  chat_id: '',
  forum_chat_id: '',
  is_authorized: false,
  bot_username: '',
  auth_code: '',
  delivery_mode: 'PRIVATE_CHAT',
  control_topic_id: null,
  early_topic_id: null,
  warning_topic_id: null,
  stop_topic_id: null,
  enable_topic_id: null,
  poller_status: 'OFFLINE',
  last_poller_heartbeat_at: null,
  auth_deep_link: '',
  activation_command: '',
  forum_cutover_status: 'NOT_STARTED',
  primary_recipient: null,
  active_invite: null,
};

const DEFAULT_VISION = {
  api_url: 'http://127.0.0.1:3030',
  x_token: '',
  profile_id: '',
  has_token: false,
};

function mergeObserverState(data) {
  return {
    interval_seconds: data.interval_seconds ?? DEFAULT_OBSERVER.interval_seconds,
    jitter_seconds: data.jitter_seconds ?? DEFAULT_OBSERVER.jitter_seconds,
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
  };
}

function mergeTelegramState(data) {
  return {
    bot_token: data.bot_token || '',
    chat_id: data.chat_id || '',
    forum_chat_id: data.forum_chat_id || '',
    is_authorized: data.is_authorized || false,
    bot_username: data.bot_username || '',
    auth_code: data.auth_code || '',
    delivery_mode: data.delivery_mode || 'PRIVATE_CHAT',
    control_topic_id: data.control_topic_id ?? null,
    early_topic_id: data.early_topic_id ?? null,
    warning_topic_id: data.warning_topic_id ?? null,
    stop_topic_id: data.stop_topic_id ?? null,
    enable_topic_id: data.enable_topic_id ?? null,
    poller_status: data.poller_status || 'OFFLINE',
    last_poller_heartbeat_at: data.last_poller_heartbeat_at || null,
    auth_deep_link: data.auth_deep_link || '',
    activation_command: data.activation_command || '',
    forum_cutover_status: data.forum_cutover_status || 'NOT_STARTED',
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
  const effectiveDeliveryMode =
    authResult?.delivery_mode || telegram.delivery_mode || 'PRIVATE_CHAT';
  const isForumMode = effectiveDeliveryMode === 'FORUM_GROUP';
  const pendingAuthCode = authResult?.auth_code || telegram.auth_code || '';
  const pendingAuthCommand =
    authResult?.activation_command ||
    telegram.activation_command ||
    (pendingAuthCode ? `/start ${pendingAuthCode}` : '');
  const pendingAuthDeepLink = isForumMode
    ? ''
    : authResult?.auth_deep_link ||
      telegram.auth_deep_link ||
      makeTelegramDeepLink(currentBotUsername, pendingAuthCode);
  const inviteDeepLink =
    telegram.delivery_mode === 'FORUM_GROUP'
      ? ''
      : inviteCode?.deep_link ||
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
  }, [loadObserverSettings, loadTelegramSettings, loadVisionSettings, loadRecipients]);

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
        interval_seconds: observer.interval_seconds,
        jitter_seconds: observer.jitter_seconds,
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
  }, [observer]);

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
  }, [loadTelegramSettings, newToken]);

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
      const code = result.code || '';
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
        profile_id: vision.profile_id,
      });
      setToast({ message: 'Vision настройки сохранены', type: 'success' });
      const visionData = await getVisionSettings();
      if (visionData && typeof visionData === 'object') {
        setVision({ ...visionData, x_token: '' });
      }
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  }, [vision]);

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
      loadProfiles,
    },
  };
}
