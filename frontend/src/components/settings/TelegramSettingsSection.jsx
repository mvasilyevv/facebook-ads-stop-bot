import { formatDateTimeRu } from './settingsUtils.js';

function RecipientCard({ recipient, onDelete }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border bg-elevated px-4 py-3">
      <div>
        <div className="text-sm font-medium text-primary">
          {recipient.first_name || recipient.username || 'Пользователь'}
          {recipient.username && <span className="ml-1 text-2xs text-muted">@{recipient.username}</span>}
        </div>
        <div className="text-2xs text-muted">Добавлен {formatDateTimeRu(recipient.created_at)}</div>
      </div>
      <button className="btn-ghost text-danger text-2xs" onClick={() => onDelete(recipient.id)} title="Удалить получателя">
        Удалить
      </button>
    </div>
  );
}

function InvitePanel({ inviteCode, inviteDeepLink, currentBotUsername, isForumMode, onOpenTelegram, onCopyCommand, onCopyLink, onDone }) {
  const inviteCommand = inviteCode.code ? `/start ${inviteCode.code}` : '';
  return (
    <div className="space-y-3 rounded-md border border-accent/30 bg-accent-muted p-4">
      <div className="text-sm font-semibold text-accent">Инвайт-код создан</div>
      <div className="text-2xs text-secondary">
        {isForumMode
          ? <>Попросите пользователя отправить команду в topic <strong>CONTROL</strong>.</>
          : <>Попросите пользователя открыть бота <strong>@{inviteCode.bot_username || currentBotUsername || '—'}</strong>.</>}
      </div>
      <div className="rounded bg-elevated px-3 py-2 font-mono text-sm text-primary">{inviteCommand || 'Код не получен'}</div>
      <div className="space-y-0.5 text-2xs text-muted">
        <div>Роль: {inviteCode.role === 'owner' ? 'Владелец' : 'Получатель'}</div>
        <div>Срок действия: {formatDateTimeRu(inviteCode.expires_at)}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary text-2xs" onClick={() => onCopyCommand(inviteCommand)} disabled={!inviteCommand}>Скопировать команду</button>
        {!isForumMode && <button className="btn-secondary text-2xs" onClick={() => onOpenTelegram(inviteDeepLink)} disabled={!inviteDeepLink}>Открыть бота</button>}
        {!isForumMode && <button className="btn-secondary text-2xs" onClick={() => onCopyLink(inviteDeepLink)} disabled={!inviteDeepLink}>Скопировать ссылку</button>}
        <button className="btn-ghost text-2xs" onClick={onDone}>Готово</button>
      </div>
    </div>
  );
}

function ConnectedTelegramPanel({ telegram, currentBotUsername, pollerStatusMeta, primaryRecipient, recipients, inviteCode, inviteDeepLink, onCreateInvite, onClearInvite, onDeleteRecipient, onRevokeTelegram, onOpenTelegram, onCopyWithToast, saving }) {
  const isForumMode = telegram.delivery_mode === 'FORUM_GROUP';
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-md border border-success/30 bg-success-muted p-4">
        <span className="text-success">✓</span>
        <div className="text-sm">
          <div className="font-semibold text-success">Подключён</div>
          <div className="mt-1 space-y-0.5 text-2xs text-secondary">
            <div>Бот: @{currentBotUsername || telegram.bot_username || '—'}</div>
            <div>Poller: <span style={{ color: pollerStatusMeta.color }}>{pollerStatusMeta.label}</span></div>
            <div>Heartbeat: {formatDateTimeRu(telegram.last_poller_heartbeat_at)}</div>
            <div>Режим: {isForumMode ? 'forum-группа' : 'личный чат'}</div>
          </div>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-2xs font-bold uppercase tracking-widest text-muted">
          {isForumMode ? 'Группа и topics' : 'Основной чат'}
        </h4>
        <div className="rounded-md border border-border bg-elevated p-4 space-y-1 text-2xs text-secondary">
          <div className="text-sm font-medium text-primary">{isForumMode ? 'Рабочая forum-группа' : 'Основной чат уведомлений'}</div>
          <div>ID чата: <code className="font-mono text-primary">{primaryRecipient?.masked_chat_id || telegram.forum_chat_id || telegram.chat_id || '—'}</code></div>
          {isForumMode && (
            <>
              <div>CONTROL: <code className="font-mono">{telegram.control_topic_id || '—'}</code></div>
              <div>Topics: <code className="font-mono">{[telegram.warning_topic_id, telegram.stop_topic_id, telegram.enable_topic_id].filter(Boolean).join(' / ') || '—'}</code></div>
            </>
          )}
          {primaryRecipient?.username && <div>Telegram: @{primaryRecipient.username}</div>}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-2xs font-bold uppercase tracking-widest text-muted">
          Участники ({recipients.length})
        </h4>
        {inviteCode ? (
          <InvitePanel inviteCode={inviteCode} inviteDeepLink={inviteDeepLink} currentBotUsername={currentBotUsername} isForumMode={isForumMode} onOpenTelegram={onOpenTelegram} onCopyCommand={(v) => onCopyWithToast(v, 'Команда скопирована')} onCopyLink={(v) => onCopyWithToast(v, 'Ссылка скопирована')} onDone={onClearInvite} />
        ) : (
          <button className="btn-secondary text-2xs" onClick={onCreateInvite} disabled={saving === 'invite'}>
            {saving === 'invite' ? 'Генерация...' : '+ Добавить пользователя'}
          </button>
        )}
        {recipients.length > 0 && (
          <div className="mt-3 space-y-2">
            {recipients.map((r) => <RecipientCard key={r.id} recipient={r} onDelete={onDeleteRecipient} />)}
          </div>
        )}
        {recipients.length === 0 && <div className="mt-2 text-2xs text-muted">Нет участников. Добавьте через инвайт-код.</div>}
      </div>

      <button className="btn-ghost text-danger text-2xs" onClick={onRevokeTelegram} disabled={saving === 'telegram'}>
        Отключить Telegram
      </button>
    </div>
  );
}

function WaitingTelegramPanel({ telegram, currentBotUsername, pollerStatusMeta, pendingAuthCommand, pendingAuthDeepLink, onOpenTelegram, onCopyWithToast, onRefreshStatus, onRevokeTelegram, authChecking }) {
  const isForumMode = telegram.delivery_mode === 'FORUM_GROUP';
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-accent/30 bg-accent-muted p-4 space-y-3">
        <div className="text-sm font-semibold text-accent">Ожидание подтверждения</div>
        <div className="text-2xs text-secondary">
          {isForumMode
            ? <>Откройте группу и отправьте команду в topic <strong>CONTROL</strong>.</>
            : <>Откройте бота <strong>@{currentBotUsername || telegram.bot_username || '—'}</strong> и отправьте команду.</>}
        </div>
        <div className="rounded bg-elevated px-3 py-2 font-mono text-sm text-primary">{pendingAuthCommand || 'Команда не найдена'}</div>
        <div className="text-2xs text-muted">Статус обновляется автоматически каждые 2 секунды.</div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary text-2xs" onClick={() => onCopyWithToast(pendingAuthCommand, 'Команда скопирована')} disabled={!pendingAuthCommand}>Скопировать</button>
        {!isForumMode && <button className="btn-secondary text-2xs" onClick={() => onOpenTelegram(pendingAuthDeepLink)} disabled={!pendingAuthDeepLink}>Открыть бота</button>}
        <button className="btn-ghost text-2xs" onClick={onRefreshStatus}>{authChecking ? 'Проверяем...' : 'Обновить статус'}</button>
        <button className="btn-ghost text-danger text-2xs" onClick={onRevokeTelegram}>Отмена</button>
      </div>
    </div>
  );
}

function DisconnectedTelegramPanel({ telegram, newToken, onNewTokenChange, onConnectTelegram, pollerStatusMeta }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-secondary">
        Подключите Telegram-бота для уведомлений. Создайте бота через <strong>@BotFather</strong>, затем вставьте токен.
      </p>
      <div>
        <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="tg-token">
          Токен бота
        </label>
        <input
          id="tg-token"
          className="w-full rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none"
          type="password"
          placeholder="123456:ABC-DEF1234ghIkl-..."
          value={newToken}
          onChange={(e) => onNewTokenChange(e.target.value)}
          autoComplete="off"
        />
        <div className="mt-1 text-2xs text-muted">Токен хранится в зашифрованном виде.</div>
      </div>
      <button className="btn-primary" onClick={onConnectTelegram} disabled={!newToken.trim()}>
        Проверить токен и получить код
      </button>
      <div className="text-2xs text-muted">
        Poller: <span style={{ color: pollerStatusMeta.color }}>{pollerStatusMeta.label}</span>
        {' · '}Heartbeat: {formatDateTimeRu(telegram.last_poller_heartbeat_at)}
      </div>
    </div>
  );
}

export function TelegramSettingsSection({ telegram, newToken, onNewTokenChange, authChecking, currentBotUsername, pollerStatusMeta, primaryRecipient, recipients, inviteCode, inviteDeepLink, pendingAuthCommand, pendingAuthDeepLink, isWaitingTelegramAuth, onConnectTelegram, onRevokeTelegram, onRefreshStatus, onDeleteRecipient, onCreateInvite, onClearInvite, onOpenTelegram, onCopyWithToast, saving }) {
  return (
    <section aria-label="Настройки Telegram" className="panel p-5 space-y-4">
      <h2 className="text-base font-semibold text-primary">Telegram — уведомления</h2>

      {telegram.is_authorized ? (
        <ConnectedTelegramPanel telegram={telegram} currentBotUsername={currentBotUsername} pollerStatusMeta={pollerStatusMeta} primaryRecipient={primaryRecipient} recipients={recipients} inviteCode={inviteCode} inviteDeepLink={inviteDeepLink} onCreateInvite={onCreateInvite} onDeleteRecipient={onDeleteRecipient} onRevokeTelegram={onRevokeTelegram} onOpenTelegram={onOpenTelegram} onCopyWithToast={onCopyWithToast} saving={saving} onClearInvite={onClearInvite} />
      ) : isWaitingTelegramAuth ? (
        <WaitingTelegramPanel telegram={telegram} currentBotUsername={currentBotUsername} pollerStatusMeta={pollerStatusMeta} pendingAuthCommand={pendingAuthCommand} pendingAuthDeepLink={pendingAuthDeepLink} onOpenTelegram={onOpenTelegram} onCopyWithToast={onCopyWithToast} onRefreshStatus={onRefreshStatus} onRevokeTelegram={onRevokeTelegram} authChecking={authChecking} />
      ) : (
        <DisconnectedTelegramPanel telegram={telegram} newToken={newToken} onNewTokenChange={onNewTokenChange} onConnectTelegram={onConnectTelegram} pollerStatusMeta={pollerStatusMeta} />
      )}
    </section>
  );
}
