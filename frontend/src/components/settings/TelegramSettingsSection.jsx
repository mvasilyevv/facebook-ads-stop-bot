import { formatDateTimeRu } from './settingsUtils.js';

function RecipientCard({ recipient, onDelete }) {
  return (
    <div className="settings-recipient-card">
      <div className="settings-recipient-card__body">
        <div className="settings-recipient-card__name">
          {recipient.first_name || recipient.username || 'Пользователь'}
          {recipient.username && (
            <span className="settings-recipient-card__username">
              @{recipient.username}
            </span>
          )}
        </div>
        <div className="settings-recipient-card__meta">
          Добавлен {formatDateTimeRu(recipient.created_at)}
        </div>
      </div>
      <button
        type="button"
        className="btn btn-outline btn-sm settings-action-danger"
        onClick={() => onDelete(recipient.id)}
        title="Удалить получателя"
      >
        Удалить
      </button>
    </div>
  );
}

function InvitePanel({
  inviteCode,
  inviteDeepLink,
  currentBotUsername,
  isForumMode,
  onOpenTelegram,
  onCopyCommand,
  onCopyLink,
  onDone,
}) {
  const inviteCommand = inviteCode.code ? `/start ${inviteCode.code}` : '';

  return (
    <div className="settings-panel settings-panel--info settings-stack">
      <div className="settings-panel__title settings-panel__title--info">
        Инвайт-код для получателя создан
      </div>
      <div className="settings-panel__text">
        {isForumMode ? (
          <>
            Попросите пользователя открыть группу <strong>AdGuard FB Bot</strong>, перейти в topic{' '}
            <strong>CONTROL</strong> и отправить команду ниже.
          </>
        ) : (
          <>
            Попросите пользователя открыть бота{' '}
            <strong>@{inviteCode.bot_username || currentBotUsername || '—'}</strong>.
          </>
        )}
      </div>
      <div className="settings-code-block">
        {inviteCommand || 'Код не получен'}
      </div>
      <div className="settings-meta-list">
        <div className="settings-meta-line">
          Активация: {isForumMode ? 'только через /start CODE в CONTROL' : inviteDeepLink || '—'}
        </div>
        <div className="settings-meta-line">
          Роль: {inviteCode.role === 'owner' ? 'Владелец' : 'Получатель'}
        </div>
        <div className="settings-meta-line">
          Срок действия: {formatDateTimeRu(inviteCode.expires_at)}
        </div>
      </div>
      <div className="settings-actions">
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={() => onCopyCommand(inviteCommand)}
          disabled={!inviteCommand}
        >
          Скопировать команду
        </button>
        {!isForumMode && (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onOpenTelegram(inviteDeepLink)}
            disabled={!inviteDeepLink}
          >
            Открыть бота
          </button>
        )}
        {!isForumMode && (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onCopyLink(inviteDeepLink)}
            disabled={!inviteDeepLink}
          >
            Скопировать ссылку
          </button>
        )}
        <button type="button" className="btn btn-outline btn-sm" onClick={onDone}>
          Готово
        </button>
      </div>
    </div>
  );
}

function ConnectedTelegramPanel({
  telegram,
  currentBotUsername,
  pollerStatusMeta,
  primaryRecipient,
  recipients,
  inviteCode,
  inviteDeepLink,
  onCreateInvite,
  onClearInvite,
  onDeleteRecipient,
  onRevokeTelegram,
  onOpenTelegram,
  onCopyWithToast,
  saving,
}) {
  const isForumMode = telegram.delivery_mode === 'FORUM_GROUP';

  return (
    <div className="settings-stack settings-stack--lg">
      <div className="settings-panel settings-panel--success">
        <div className="settings-panel__header">
          <span className="settings-panel__icon" aria-hidden="true">✅</span>
          <div className="settings-panel__body">
            <div className="settings-panel__title settings-panel__title--success">Подключён</div>
            <div className="settings-meta-line">
              Бот: @{currentBotUsername || telegram.bot_username || '—'}
            </div>
            <div className="settings-meta-line">
              Poller:{' '}
              <span style={{ color: pollerStatusMeta.color }}>{pollerStatusMeta.label}</span>
            </div>
            <div className="settings-meta-line">
              Heartbeat: {formatDateTimeRu(telegram.last_poller_heartbeat_at)}
            </div>
            <div className="settings-meta-line">
              Режим: {isForumMode ? 'forum-группа' : 'личный чат'}
            </div>
          </div>
        </div>
      </div>

      <div className="settings-block">
        <div className="settings-block__title">
          {isForumMode ? 'Группа и topics' : 'Основной чат'}
        </div>
        <div className="settings-card">
          <div className="settings-card__title">
            {isForumMode ? 'Рабочая forum-группа' : 'Основной чат уведомлений'}
          </div>
          <div className="settings-card__text">
            {isForumMode
              ? 'Уведомления разведены по topics, а общее управление перенесено в CONTROL.'
              : 'Все алерты, задачи и служебные сообщения приходят сюда.'}
          </div>
          <div className="settings-meta-line">
            ID чата:{' '}
            <code>
              {primaryRecipient?.masked_chat_id ||
                telegram.forum_chat_id ||
                telegram.chat_id ||
                '—'}
            </code>
          </div>
          {isForumMode && (
            <>
              <div className="settings-meta-line">
                CONTROL: <code>{telegram.control_topic_id || '—'}</code>
              </div>
              <div className="settings-meta-line">
                EARLY / WARNING / STOP / ENABLE:{' '}
                <code>
                  {[
                    telegram.early_topic_id,
                    telegram.warning_topic_id,
                    telegram.stop_topic_id,
                    telegram.enable_topic_id,
                  ]
                    .filter(Boolean)
                    .join(' / ') || '—'}
                </code>
              </div>
              <div className="settings-meta-line">
                Статус переезда: {telegram.forum_cutover_status || '—'}
              </div>
            </>
          )}
          {primaryRecipient?.username && (
            <div className="settings-meta-line">
              Telegram: @{primaryRecipient.username}
            </div>
          )}
          {primaryRecipient?.first_name && (
            <div className="settings-meta-line">
              Имя: {primaryRecipient.first_name}
            </div>
          )}
        </div>
      </div>

      <div className="settings-block">
        <div className="settings-block__title">
          Авторизованные участники группы ({recipients.length})
        </div>

        {inviteCode ? (
          <InvitePanel
            inviteCode={inviteCode}
            inviteDeepLink={inviteDeepLink}
            currentBotUsername={currentBotUsername}
            isForumMode={isForumMode}
            onOpenTelegram={onOpenTelegram}
            onCopyCommand={(value) => onCopyWithToast(value, 'Команда скопирована')}
            onCopyLink={(value) => onCopyWithToast(value, 'Ссылка скопирована')}
            onDone={onClearInvite}
          />
        ) : (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={onCreateInvite}
            disabled={saving === 'invite'}
          >
            {saving === 'invite' ? 'Генерация...' : '+ Добавить пользователя'}
          </button>
        )}

        {recipients.length > 0 ? (
          <div className="settings-stack settings-stack--sm settings-block__content">
            {recipients.map((recipient) => (
              <RecipientCard
                key={recipient.id}
                recipient={recipient}
                onDelete={onDeleteRecipient}
              />
            ))}
          </div>
        ) : (
          <div className="settings-empty-text settings-block__content">
            Пока авторизованных участников нет. Добавьте пользователя через инвайт-код выше.
          </div>
        )}
      </div>

      <div className="settings-actions">
        <button
          type="button"
          className="btn btn-outline btn-sm settings-action-danger"
          onClick={onRevokeTelegram}
          disabled={saving === 'telegram'}
        >
          Отключить Telegram
        </button>
      </div>
    </div>
  );
}

function WaitingTelegramPanel({
  telegram,
  currentBotUsername,
  pollerStatusMeta,
  pendingAuthCommand,
  pendingAuthDeepLink,
  onOpenTelegram,
  onCopyWithToast,
  onRefreshStatus,
  onRevokeTelegram,
  authChecking,
}) {
  const isForumMode = telegram.delivery_mode === 'FORUM_GROUP';

  return (
    <div className="settings-stack settings-stack--lg">
      <div className="settings-panel settings-panel--info settings-stack">
        <div className="settings-panel__title settings-panel__title--info">
          Ожидание подтверждения
        </div>
        <div className="settings-panel__text">
          {isForumMode ? (
            <>
              Откройте группу <strong>AdGuard FB Bot</strong>, перейдите в topic{' '}
              <strong>CONTROL</strong> и отправьте команду подключения.
            </>
          ) : (
            <>
              Откройте бота{' '}
              <strong>@{currentBotUsername || telegram.bot_username || '—'}</strong>{' '}
              и отправьте команду подключения.
            </>
          )}
        </div>
        <div className="settings-meta-list">
          <div className="settings-meta-line">
            Poller:{' '}
            <span style={{ color: pollerStatusMeta.color }}>{pollerStatusMeta.label}</span>
          </div>
          {isForumMode && (
            <div className="settings-meta-line">
              Рабочая группа: <code>{telegram.forum_chat_id || telegram.chat_id || '—'}</code>
            </div>
          )}
          <div className="settings-meta-line">
            Heartbeat: {formatDateTimeRu(telegram.last_poller_heartbeat_at)}
          </div>
          <div className="settings-meta-line">
            Статус обновляется автоматически каждые 2 секунды.
          </div>
        </div>
        <div className="settings-code-block">
          {pendingAuthCommand || 'Команда подключения не найдена'}
        </div>
        <div className="settings-meta-list">
          <div className="settings-meta-line">
            Активация: {isForumMode ? 'только через /start CODE в CONTROL' : pendingAuthDeepLink || '—'}
          </div>
          <div className="settings-meta-line">
            После успешного `/start` бот откроет CONTROL-меню автоматически.
          </div>
        </div>
      </div>

      <div className="settings-actions">
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={() => onCopyWithToast(pendingAuthCommand, 'Команда подключения скопирована')}
          disabled={!pendingAuthCommand}
        >
          Скопировать команду
        </button>
        {!isForumMode && (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onOpenTelegram(pendingAuthDeepLink)}
            disabled={!pendingAuthDeepLink}
          >
            Открыть бота
          </button>
        )}
        {!isForumMode && (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onCopyWithToast(pendingAuthDeepLink, 'Ссылка на бота скопирована')}
            disabled={!pendingAuthDeepLink}
          >
            Скопировать ссылку
          </button>
        )}
        <button type="button" className="btn btn-outline btn-sm" onClick={onRefreshStatus}>
          {authChecking ? 'Проверяем...' : 'Обновить статус'}
        </button>
        <button
          type="button"
          className="btn btn-outline btn-sm settings-action-danger"
          onClick={onRevokeTelegram}
        >
          Отмена
        </button>
      </div>
    </div>
  );
}

function DisconnectedTelegramPanel({
  telegram,
  newToken,
  onNewTokenChange,
  onConnectTelegram,
  pollerStatusMeta,
}) {
  return (
    <div className="settings-stack settings-stack--lg">
      <div className="settings-panel__text">
        Подключите Telegram-бота, чтобы получать уведомления и управлять объявлениями.
        Сначала создайте бота через <strong>@BotFather</strong>, затем вставьте токен ниже.
      </div>

      <div className="form-group settings-form-block">
        <label className="form-label" htmlFor="tg-token">
          Токен бота
        </label>
        <input
          id="tg-token"
          className="form-input"
          type="password"
          placeholder="123456:ABC-DEF1234ghIkl-..."
          value={newToken}
          onChange={(event) => onNewTokenChange(event.target.value)}
          autoComplete="off"
        />
        <div className="form-hint">
          Токен из @BotFather. Он хранится в зашифрованном виде.
        </div>
      </div>

      <div className="settings-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onConnectTelegram}
          disabled={!newToken.trim()}
        >
          Проверить токен и получить код
        </button>
      </div>

      <div className="settings-card">
        <div className="settings-card__title">После проверки токена</div>
        <div className="settings-card__text">
          Мы подготовим forum-cutover и покажем команду `/start CODE`, которую нужно отправить
          в topic <strong>CONTROL</strong> группы <strong>AdGuard FB Bot</strong>.
        </div>
        <div className="settings-meta-line">
          Poller:{' '}
          <span style={{ color: pollerStatusMeta.color }}>{pollerStatusMeta.label}</span>
          {' · '}
          Heartbeat: {formatDateTimeRu(telegram.last_poller_heartbeat_at)}
        </div>
      </div>
    </div>
  );
}

export function TelegramSettingsSection({
  telegram,
  newToken,
  onNewTokenChange,
  authChecking,
  currentBotUsername,
  pollerStatusMeta,
  primaryRecipient,
  recipients,
  inviteCode,
  inviteDeepLink,
  pendingAuthCommand,
  pendingAuthDeepLink,
  isWaitingTelegramAuth,
  onConnectTelegram,
  onRevokeTelegram,
  onRefreshStatus,
  onDeleteRecipient,
  onCreateInvite,
  onClearInvite,
  onOpenTelegram,
  onCopyWithToast,
  saving,
}) {
  return (
    <section aria-label="Настройки Telegram" className="form-section">
      <div className="form-section-title">Telegram — уведомления</div>

      {telegram.is_authorized ? (
        <ConnectedTelegramPanel
          telegram={telegram}
          currentBotUsername={currentBotUsername}
          pollerStatusMeta={pollerStatusMeta}
          primaryRecipient={primaryRecipient}
          recipients={recipients}
          inviteCode={inviteCode}
          inviteDeepLink={inviteDeepLink}
          onCreateInvite={onCreateInvite}
          onDeleteRecipient={onDeleteRecipient}
          onRevokeTelegram={onRevokeTelegram}
          onOpenTelegram={onOpenTelegram}
          onCopyWithToast={onCopyWithToast}
          saving={saving}
          onClearInvite={onClearInvite}
        />
      ) : isWaitingTelegramAuth ? (
        <WaitingTelegramPanel
          telegram={telegram}
          currentBotUsername={currentBotUsername}
          pollerStatusMeta={pollerStatusMeta}
          pendingAuthCommand={pendingAuthCommand}
          pendingAuthDeepLink={pendingAuthDeepLink}
          onOpenTelegram={onOpenTelegram}
          onCopyWithToast={onCopyWithToast}
          onRefreshStatus={onRefreshStatus}
          onRevokeTelegram={onRevokeTelegram}
          authChecking={authChecking}
        />
      ) : (
        <DisconnectedTelegramPanel
          telegram={telegram}
          newToken={newToken}
          onNewTokenChange={onNewTokenChange}
          onConnectTelegram={onConnectTelegram}
          pollerStatusMeta={pollerStatusMeta}
        />
      )}
    </section>
  );
}
