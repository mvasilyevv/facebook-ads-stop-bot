import { chromium, type Browser, type BrowserContext, type Page } from 'playwright';
import { v4 as uuidv4 } from 'uuid';
import { VisionClient } from './vision-client.js';
import { STEALTH_INIT_SCRIPT } from './stealth.js';
import { generateHumanProfile } from './humanizer.js';
import { adsManagerColumnsQs } from './am/am-columns-preset.js';
import { raceWithAbort } from './in-page-abort.js';
import { withPageRoleLock } from './page-lock.js';
import type { BrowserPageRole, BrowserSession, HumanProfile } from './types.js';

const EXISTING_PROFILE_PORT_GRACE_SECONDS = 8;
const START_PROFILE_PORT_WAIT_SECONDS = 20;
const CDP_READY_WAIT_SECONDS = 20;
const RECOVERY_STOP_TIMEOUT_SECONDS = 20;
const RECOVERY_SETTLE_DELAY_MS = 1_000;

export function isAdsManagerUrl(url: string | null | undefined): boolean {
  try {
    const parsed = new URL(String(url || ''));
    const hostname = parsed.hostname.toLowerCase();
    const pathname = parsed.pathname.toLowerCase();

    // Проверяем только фактический origin/path вкладки. Поиск подстроки во всём URL
    // ошибочно принимал business/loginpage за Ads Manager, когда его query-параметр
    // next= содержал закодированный adsmanager.facebook.com. В результате Meta API
    // читал DOM страницы входа и ложно сообщал token_not_found при живом кабинете.
    if (hostname === 'adsmanager.facebook.com') {
      return true;
    }

    const isFacebookHost = hostname === 'facebook.com' || hostname.endsWith('.facebook.com');
    return isFacebookHost && pathname.split('/').includes('adsmanager');
  } catch {
    return false;
  }
}

/** Достаёт numeric ad-account id из URL Ads Manager (?act=<num>). null, если не читается. */
export function extractAdAccountId(url: string | null | undefined): string | null {
  const m = String(url || '').match(/[?&]act=(\d+)/);
  return m ? m[1] : null;
}

/** URL Ads Manager для конкретного кабинета (мульти-кабинет, act без префикса act_).
 * Уровень КАМПАНИЙ + набор колонок пользователя (am-columns-preset) — пользователь
 * сразу видит нужные метрики; на скан (am_tabular level=ad через fetch) уровень
 * вкладки не влияет. */
export function adsManagerUrlForAct(actId: string): string {
  return (
    `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${actId}` +
    `&${adsManagerColumnsQs()}`
  );
}

/** Найти среди ВСЕХ открытых вкладок живую вкладку Ads Manager нужного кабинета. */
export function findAdsManagerPageByAct(
  browser: Browser | null,
  actId: string,
  excludedPages: ReadonlySet<Page> = new Set(),
): Page | null {
  if (!browser) {
    return null;
  }
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (typeof page.isClosed === 'function' && page.isClosed()) {
        continue;
      }
      if (excludedPages.has(page)) {
        continue;
      }
      if (isAdsManagerUrl(page.url()) && extractAdAccountId(page.url()) === actId) {
        return page;
      }
    }
  }
  return null;
}

function isPageClosed(page: Page): boolean {
  return typeof page.isClosed === 'function' && page.isClosed();
}

export function findPreferredPrimaryPage(browser: Browser | null): Page | null {
  if (!browser) {
    return null;
  }

  let fallbackPage: Page | null = null;
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (isPageClosed(page)) {
        continue;
      }
      fallbackPage = fallbackPage || page;
      if (isAdsManagerUrl(page.url())) {
        return page;
      }
    }
  }

  return fallbackPage;
}

/** Найти «нейтральную» вкладку для переиспользования под новый кабинет: исходную FB-вкладку
 * (любой facebook.com, КРОМЕ вкладки конкретного кабинета ?act=) или пустую (about:blank).
 * Кабинетные вкладки (?act=) и чужие сайты НЕ трогаем. Нужна, чтобы первый кабинет занял уже
 * открытую вкладку, а не плодил новые: при 1 кабинете — 1 вкладка вместо «исходная + кабинет». */
export function findReusableNonCabinetPage(
  browser: Browser | null,
  excludedPages: ReadonlySet<Page> = new Set(),
): Page | null {
  if (!browser) {
    return null;
  }
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (isPageClosed(page)) {
        continue;
      }
      if (excludedPages.has(page)) {
        continue;
      }
      const url = page.url() || '';
      // Вкладку конкретного кабинета не трогаем — она принадлежит другому кабинету.
      if (extractAdAccountId(url)) {
        continue;
      }
      if (url === '' || url === 'about:blank') {
        return page;
      }
      try {
        if (new URL(url).hostname.endsWith('facebook.com')) {
          return page;
        }
      } catch {
        // нераспознаваемый URL — не переиспользуем
      }
    }
  }
  return null;
}

/** Закрывает кабинетные вкладки (?act=), которых НЕТ в наборе кабинетов офферов (keepActIds).
 * Чтобы не копить лишние вкладки (напр. дефолтный кабинет профиля, не привязанный к офферу).
 * Обычные/пустые вкладки и вкладки нужных кабинетов НЕ трогает. Страховка: не закрывает
 * последнюю открытую вкладку (иначе браузер мог бы закрыться). Best-effort: ошибка close одной
 * вкладки не валит остальные. Возвращает число закрытых. */
export async function closeForeignCabinetTabs(
  browser: Browser | null,
  keepActIds: string[],
): Promise<number> {
  if (!browser) {
    return 0;
  }
  const keep = new Set(keepActIds.map((a) => String(a)));
  const openPages = (): Page[] => {
    const pages: Page[] = [];
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        if (!isPageClosed(page)) {
          pages.push(page);
        }
      }
    }
    return pages;
  };
  let closed = 0;
  for (const page of openPages()) {
    if (isPageClosed(page)) {
      continue;
    }
    const act = extractAdAccountId(page.url());
    if (!act || keep.has(act)) {
      continue; // не кабинетная вкладка ИЛИ кабинет из офферов — оставляем
    }
    if (openPages().length <= 1) {
      break; // не оставляем браузер без единой вкладки
    }
    try {
      console.warn(`[session-manager] закрываю кабинетную вкладку вне офферов: act=${act}`);
      await page.close();
      closed += 1;
    } catch {
      // best-effort: не смогли закрыть — пропускаем
    }
  }
  return closed;
}

/** Запоминает URL живой вкладки Ads Manager на сессии — чтобы переоткрыть её при self-heal. */
export function rememberAdsManagerUrl(session: BrowserSession, page: Page | null | undefined): void {
  try {
    const url = page?.url?.();
    if (url && isAdsManagerUrl(url)) {
      session.lastAdsManagerUrl = url;
    }
  } catch {
    // url() может бросить на закрытой/переходной странице — игнорируем.
  }
}

/** Менеджер браузерных сессий: запуск, подключение, отключение, переподключение. */
export class SessionManager {
  private sessions = new Map<string, BrowserSession>();
  /**
   * A page whose evaluate() did not settle after local cancellation must never
   * be selected again. Playwright close is best effort and can itself stall
   * when CDP is unhealthy, so selection quarantine is the authoritative guard.
   * Weak membership is essential: a cancelled/hung flood must not make this
   * manager retain every historical Playwright Page object graph forever.
   */
  private poisonedPages = new WeakSet<Page>();

  async startBrowser(options: {
    visionXToken: string;
    visionApiUrl: string;
    visionProfileId: string;
    visionFolderId?: string;
    viewportWidth?: number;
    viewportHeight?: number;
    forceProfileRestart?: boolean;
    signal?: AbortSignal;
  }): Promise<BrowserSession> {
    const {
      visionXToken,
      visionApiUrl,
      visionProfileId,
      visionFolderId,
      forceProfileRestart = false,
      signal,
    } = options;
    throwIfOperationAborted(signal);

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Vision API иногда требует folder_id отдельно, поэтому восстанавливаем его по profile_id.
    let folderId = visionFolderId;
    if (!folderId) {
      folderId = await visionClient.resolveFolderId(visionProfileId, signal);
    }

    console.log(`[session-manager] startBrowser: profile=${visionProfileId} folder=${folderId}`);
    const existingProfile = await visionClient.getProfile(visionProfileId, signal);
    console.log(
      `[session-manager] /list для ${visionProfileId}: ${
        existingProfile ? `port=${existingProfile.port}` : 'НЕТ в списке'
      }`,
    );
    let profile: { port: number | null };

    if (existingProfile && forceProfileRestart) {
      console.log(
        `[session-manager] explicit maintenance recovery: restart profile ${visionProfileId}`,
      );
      profile = await this.restartProfileForMissingCdp(
        visionClient,
        folderId,
        visionProfileId,
        signal,
      );
    } else if (existingProfile?.port) {
      // Не стартуем второй экземпляр профиля, иначе можно потерять открытую вкладку.
      console.log(`[session-manager] профиль уже с CDP-портом ${existingProfile.port}, использую как есть`);
      profile = { port: existingProfile.port };
    } else if (existingProfile) {
      // У Vision порт иногда появляется с задержкой, поэтому сначала даем ему короткий grace period.
      console.log(`[session-manager] профиль без CDP, жду до ${EXISTING_PROFILE_PORT_GRACE_SECONDS}с`);
      const delayedPort = await visionClient.waitUntilProfileHasPort(
        visionProfileId,
        EXISTING_PROFILE_PORT_GRACE_SECONDS,
        1,
        signal,
      );
      if (delayedPort) {
        console.log(`[session-manager] порт появился сам: ${delayedPort}`);
        profile = { port: delayedPort };
      } else {
        throw buildMaintenanceRecoveryRequiredError(visionProfileId);
      }
    } else if (forceProfileRestart) {
      console.log(`[session-manager] maintenance recovery starts stopped profile`);
      profile = await visionClient.startProfile(folderId, visionProfileId, {
        portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
        signal,
      });
      console.log(`[session-manager] /start вернул port=${profile.port}`);
    } else {
      throw buildMaintenanceRecoveryRequiredError(visionProfileId);
    }

    if (!profile.port) {
      throw new Error(`У профиля ${visionProfileId} нет CDP-порта`);
    }

    // Подключаемся через CDP как внешний клиент, не владеющий жизненным циклом браузера.
    console.log(`[session-manager] подключаюсь по CDP к порту ${profile.port}`);
    const playwright = chromium;
    let browser: Browser;
    try {
      browser = await this.connectOverReadyCdp(
        visionClient,
        visionProfileId,
        profile.port,
        signal,
      );
      console.log(`[session-manager] CDP-подключение установлено`);
    } catch (error) {
      console.log(
        `[session-manager] connectOverReadyCdp упал на порту ${profile.port}: ${
          error instanceof Error ? error.stack || error.message : String(error)
        }`,
      );
      throw error;
    }

    // Stealth добавляем в существующий контекст, не пересоздавая профиль и вкладки.
    const contexts = browser.contexts();
    if (contexts.length > 0) {
      await contexts[0].addInitScript(STEALTH_INIT_SCRIPT);
    }
    let primaryPage = findPreferredPrimaryPage(browser);
    if (!primaryPage && contexts[0]) {
      primaryPage = await contexts[0].newPage();
    }
    throwIfOperationAborted(signal);
    // Для CDP-вкладки Vision нельзя насильно ставить setViewportSize:
    // Playwright включает эмуляцию viewport и справа появляется белая полоса,
    // если реальное окно профиля шире 1280px. Сохраняем нативную геометрию окна.

    // Профиль "человечности" фиксируем на сессию, чтобы движения не прыгали между вызовами.
    const humanProfile = generateHumanProfile();

    const session: BrowserSession = {
      id: uuidv4(),
      visionApiUrl,
      visionXToken,
      visionProfileId,
      visionFolderId: folderId,
      cdpPort: profile.port,
      playwright,
      browser,
      primaryPage,
      scanPages: new Map(),
      controlPages: new Map(),
      interactivePages: new Map(),
      humanProfile,
      connectedAt: new Date(),
      status: 'connected',
    };

    this.sessions.set(session.id, session);
    return session;
  }

  async recoverBrowserProfileUnderMaintenance(options: {
    visionXToken: string;
    visionApiUrl: string;
    visionProfileId: string;
    visionFolderId?: string;
  }, signal?: AbortSignal): Promise<BrowserSession> {
    throwIfOperationAborted(signal);
    const matchingSessions = Array.from(this.sessions.values())
      .filter((session) => session.visionProfileId === options.visionProfileId)
      .sort((left, right) => right.connectedAt.getTime() - left.connectedAt.getTime());
    const currentSession = matchingSessions[0];

    if (currentSession) {
      const recovered = await this.reconnectBrowserWithConfig(currentSession, {
        visionXToken: options.visionXToken,
        visionApiUrl: options.visionApiUrl,
        visionProfileId: options.visionProfileId,
        forceProfileRestart: true,
        signal,
      });
      for (const staleSession of matchingSessions.slice(1)) {
        this.sessions.delete(staleSession.id);
      }
      return recovered;
    }

    return this.startBrowser({
      ...options,
      forceProfileRestart: true,
      signal,
    });
  }

  async reconnectBrowser(sessionId: string, options?: {
    signal?: AbortSignal;
  }): Promise<BrowserSession> {
    const session = this.getSession(sessionId);
    // Ordinary reconnect is intentionally incapable of accepting replacement
    // credentials, endpoint or profile. Retarget/restart is a separate
    // maintenance-only method reached after the durable capability boundary.
    return this.reconnectBrowserWithConfig(session, {
      visionXToken: session.visionXToken,
      visionApiUrl: session.visionApiUrl,
      visionProfileId: session.visionProfileId,
      forceProfileRestart: false,
      signal: options?.signal,
    });
  }

  private async reconnectBrowserWithConfig(
    session: BrowserSession,
    options: {
      visionXToken: string;
      visionApiUrl: string;
      visionProfileId: string;
      forceProfileRestart: boolean;
      signal?: AbortSignal;
    },
  ): Promise<BrowserSession> {
    const signal = options?.signal;
    throwIfOperationAborted(signal);
    // Старый CDP-клиент — отвяжем его ПОСЛЕ успешного нового подключения (H-6/BA-2),
    // чтобы не копить ws-соединения и listeners под recovery-нагрузкой.
    const oldBrowser = session.browser;

    const visionXToken = options.visionXToken;
    const visionApiUrl = options.visionApiUrl;
    const visionProfileId = options.visionProfileId;

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Переподключение сначала пытается забрать уже существующий CDP-порт и не трогать окно профиля.
    const existingProfile = await visionClient.getProfile(visionProfileId, signal);
    const resolvedFolderId = session.visionProfileId === visionProfileId
      ? session.visionFolderId
      : await visionClient.resolveFolderId(visionProfileId, signal);

    const forceRestart = options.forceProfileRestart;
    let resolvedPort = forceRestart ? null : (existingProfile?.port ?? null);
    if (!resolvedPort && existingProfile && !forceRestart) {
      resolvedPort = await visionClient.waitUntilProfileHasPort(
        visionProfileId,
        EXISTING_PROFILE_PORT_GRACE_SECONDS,
        1,
        signal,
      );
    }

    if (!resolvedPort && existingProfile && forceRestart) {
      const recoveredProfile = await this.restartProfileForMissingCdp(
        visionClient,
        resolvedFolderId,
        visionProfileId,
        signal,
      );
      resolvedPort = recoveredProfile.port;
    }

    if (!resolvedPort) {
      throw buildMaintenanceRecoveryRequiredError(visionProfileId);
    }

    const browser = await this.connectOverReadyCdp(
      visionClient,
      visionProfileId,
      resolvedPort,
      signal,
    );

    // Повторно добавляем stealth в существующий контекст после нового CDP-подключения.
    const contexts = browser.contexts();
    if (contexts.length > 0) {
      await contexts[0].addInitScript(STEALTH_INIT_SCRIPT);
    }

    // Сохраняем текущую вкладку как primaryPage, чтобы восстановить работу без навигации.
    let primaryPage = findPreferredPrimaryPage(browser);
    if (!primaryPage && contexts[0]) {
      primaryPage = await contexts[0].newPage();
    }
    throwIfOperationAborted(signal);

    session.browser = browser;
    session.primaryPage = primaryPage;
    // Page proxies from the previous CDP connection are never reused. Roles
    // will be rebuilt lazily and isolation is re-checked on first access.
    session.scanPages = new Map();
    session.controlPages = new Map();
    session.interactivePages = new Map();
    session.playwright = chromium;
    session.cdpPort = resolvedPort;
    session.status = 'connected';
    session.connectedAt = new Date();
    session.visionXToken = visionXToken;
    session.visionApiUrl = visionApiUrl;
    session.visionProfileId = visionProfileId;
    session.visionFolderId = resolvedFolderId;

    // H-6 (BA-2): отвязываем старый CDP-клиент. browser.close() звать НЕЛЬЗЯ — для
    // connectOverCDP он закрыл бы удалённый Vision-профиль, к которому мы только что
    // переподключились. Снимаем наши listeners и роняем ссылку → GC соберёт старый
    // Browser вместе с его ws-транспортом, не накапливая соединения при recovery.
    if (oldBrowser && oldBrowser !== browser) {
      try {
        oldBrowser.removeAllListeners();
      } catch {
        // best-effort: старый клиент мог уже умереть (из-за чего и реконнектимся).
      }
    }

    return session;
  }

  // A network failure may reload only the concrete role/cabinet page. CDP
  // reconnect and Vision profile restart are lifecycle-changing operations and
  // are allowed only through the PostgreSQL-backed exclusive maintenance path.
  async reloadPageAfterNetworkFailure(
    sessionId: string,
    opts: { role: BrowserPageRole; actId?: string; page?: Page; signal?: AbortSignal },
  ): Promise<{ action: string; ok: boolean }> {
    return withPageRoleLock(
      sessionId,
      opts.role,
      opts.actId,
      () => this.reloadPageAfterNetworkFailureWithinRoleLock(sessionId, opts),
    );
  }

  /**
   * Reload the concrete role page while the caller still owns its role lock.
   *
   * Graph/scan handlers use this variant so the failed fetch, recovery reload
   * and RPC completion are one serialized browser operation.  A second RPC
   * cannot enter between the failed fetch and the reload.
   */
  async reloadPageAfterNetworkFailureWithinRoleLock(
    sessionId: string,
    opts: { role: BrowserPageRole; actId?: string; page?: Page; signal?: AbortSignal },
  ): Promise<{ action: string; ok: boolean }> {
    const session = this.getSession(sessionId);
    let ok = true;
    const action = 'reload';
    try {
      const page = opts.page;
      const closed = typeof page?.isClosed === 'function' && page.isClosed();
      if (page && !closed) {
        await reloadPageWithinOperation(page, opts.signal);
      }
    } catch (err) {
      if (opts.signal?.aborted) {
        throw err;
      }
      ok = false;
      console.error(`[heal] session=${sessionId} page reload failed:`, err);
    }
    session.lastHealAt = new Date();
    session.netFailureStreak = 0;
    console.warn(
      `[heal] session=${sessionId} action=${action} ok=${ok}`,
    );
    return { action, ok };
  }

  getSession(sessionId: string): BrowserSession {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error(`Сессия ${sessionId} не найдена`);
    }
    return session;
  }

  getPreferredSession(): BrowserSession {
    const sessions = Array.from(this.sessions.values())
      .filter((session) => session.status === 'connected' && session.browser)
      .sort((left, right) => right.connectedAt.getTime() - left.connectedAt.getTime());

    const adsSession = sessions.find((session) => {
      const preferredPage = findPreferredPrimaryPage(session.browser);
      return preferredPage ? isAdsManagerUrl(preferredPage.url()) : false;
    });
    const session = adsSession || sessions[0];
    if (!session) {
      throw new Error('Активная browser-agent сессия не найдена');
    }
    return session;
  }

  getSessionForVisionProfile(profileId: string): BrowserSession {
    const normalizedProfileId = String(profileId || '').trim();
    if (!normalizedProfileId) {
      throw new Error('Canonical Vision profile id is required');
    }
    const session = Array.from(this.sessions.values())
      .filter(
        (candidate) =>
          candidate.status === 'connected'
          && candidate.browser
          && candidate.visionProfileId === normalizedProfileId,
      )
      .sort((left, right) => right.connectedAt.getTime() - left.connectedAt.getTime())[0];
    if (!session) {
      throw new Error(`Active session for Vision profile ${normalizedProfileId} not found`);
    }
    return session;
  }

  /** Return the dedicated scan page for one cabinet. */
  async ensureScanPage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, 'scan', opts);
  }

  /** Return the dedicated control/Meta-mutation page for one cabinet. */
  async ensureControlPage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, 'control', opts);
  }

  /** Return the dedicated non-money Graph/media page for one cabinet. */
  async ensureInteractivePage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, 'interactive', opts);
  }

  /**
   * Remove one exact role page synchronously and quarantine it before starting
   * best-effort close. The next operation can therefore allocate a replacement
   * without waiting for the abandoned CDP command or accidentally reusing it.
   */
  poisonRolePage(
    session: BrowserSession,
    role: BrowserPageRole,
    actId: string,
    page: Page,
  ): void {
    const cabinetKey = String(actId || '').replace(/^act_/, '').trim() || '__default__';
    const rolePages: Record<BrowserPageRole, Map<string, Page>> = {
      scan: session.scanPages,
      control: session.controlPages,
      interactive: session.interactivePages,
    };
    if (rolePages[role]?.get(cabinetKey) === page) {
      rolePages[role].delete(cabinetKey);
    }
    if (role === 'scan' && session.primaryPage === page) {
      session.primaryPage = null;
    }
    this.poisonedPages.add(page);
    void page.close().catch(() => undefined);
  }

  private async ensureRolePage(
    session: BrowserSession,
    role: BrowserPageRole,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal },
  ): Promise<Page> {
    throwIfOperationAborted(opts.signal);
    session.scanPages ??= new Map();
    session.controlPages ??= new Map();
    session.interactivePages ??= new Map();

    const browser = session.browser;
    const alive = browser && (typeof browser.isConnected !== 'function' || browser.isConnected());
    const context = alive ? browser.contexts()[0] : undefined;
    if (!browser || !context) {
      throw new Error('Основная страница браузера недоступна');
    }

    const explicitAct = String(opts.actId || '').replace(/^act_/, '').trim();
    const preferredPage = findPreferredPrimaryPage(browser);
    const sourceUrl = opts.fallbackUrl
      || session.lastAdsManagerUrl
      || (isAdsManagerUrl(session.primaryPage?.url?.()) ? session.primaryPage?.url() : undefined)
      || (isAdsManagerUrl(preferredPage?.url?.()) ? preferredPage?.url() : undefined);
    const resolvedAct = explicitAct || extractAdAccountId(sourceUrl) || '';
    const cabinetKey = resolvedAct || '__default__';
    const sourceMatchesAct = resolvedAct && extractAdAccountId(sourceUrl) === resolvedAct;
    const targetUrl = sourceMatchesAct ? sourceUrl : resolvedAct ? adsManagerUrlForAct(resolvedAct) : sourceUrl;
    if (!targetUrl || !isAdsManagerUrl(targetUrl)) {
      throw new Error(
        `Основная страница браузера недоступна: ${role} кабинет не определён`,
      );
    }

    const rolePages: Record<BrowserPageRole, Map<string, Page>> = {
      scan: session.scanPages,
      control: session.controlPages,
      interactive: session.interactivePages,
    };
    const ownPages = rolePages[role];
    const opposite = new Set<Page>();
    for (const [otherRole, pages] of Object.entries(rolePages)) {
      if (otherRole === role) continue;
      for (const page of pages.values()) opposite.add(page);
    }
    const mapped = ownPages.get(cabinetKey);
    const mappedMatchesAct = !resolvedAct || extractAdAccountId(mapped?.url?.()) === resolvedAct;
    if (
      mapped
      && !isPageClosed(mapped)
      && !this.poisonedPages.has(mapped)
      && mappedMatchesAct
      && !opposite.has(mapped)
    ) {
      throwIfOperationAborted(opts.signal);
      return mapped;
    }
    ownPages.delete(cabinetKey);

    // A page assigned to the opposite role is never eligible. Pages assigned
    // to another cabinet of the same role are excluded as well.
    const reserved = new Set<Page>(opposite);
    // WeakSet is intentionally non-enumerable. Materialize only the currently
    // live context pages for this selection call; the temporary Set disappears
    // after the call and cannot retain abandoned page graphs globally.
    for (const candidate of context.pages()) {
      if (this.poisonedPages.has(candidate)) reserved.add(candidate);
    }
    for (const [key, page] of ownPages) {
      if (key !== cabinetKey) reserved.add(page);
    }

    let page: Page | null = resolvedAct
      ? findAdsManagerPageByAct(browser, resolvedAct, reserved)
      : null;
    if (!page && !resolvedAct) {
      const preferred = findPreferredPrimaryPage(browser);
      if (preferred && !reserved.has(preferred) && isAdsManagerUrl(preferred.url())) {
        page = preferred;
      }
    }

    if (!page && explicitAct) {
      const reusable = findReusableNonCabinetPage(browser, reserved);
      if (reusable) {
        await navigatePageWithinOperation(reusable, targetUrl, opts.signal);
        page = reusable;
      }
    }
    if (!page) {
      page = await createPageWithinOperation(context, opts.signal);
      try {
        await navigatePageWithinOperation(page, targetUrl, opts.signal);
      } catch (error) {
        if (opts.signal?.aborted && !isPageClosed(page)) {
          await page.close().catch(() => undefined);
        }
        throw error;
      }
    }

    throwIfOperationAborted(opts.signal);
    if (opposite.has(page)) {
      // Fail closed: never silently degrade to a shared page.
      throw new Error(`Нарушение изоляции: ${role} page уже принадлежит другой роли`);
    }
    ownPages.set(cabinetKey, page);
    session.status = 'connected';
    rememberAdsManagerUrl(session, page);
    if (role === 'scan') session.primaryPage = page;
    return page;
  }

  listSessions(): Array<{ id: string; status: string; connectedAt: string }> {
    const result: Array<{ id: string; status: string; connectedAt: string }> = [];
    for (const [id, session] of this.sessions) {
      result.push({
        id,
        status: session.status,
        connectedAt: session.connectedAt.toISOString(),
      });
    }
    return result;
  }

  private async connectOverReadyCdp(
    visionClient: VisionClient,
    profileId: string,
    port: number,
    signal?: AbortSignal,
  ): Promise<Browser> {
    const ready = await visionClient.waitUntilCdpReady(
      port,
      CDP_READY_WAIT_SECONDS,
      1,
      signal,
    );
    if (!ready) {
      throw new Error(`CDP endpoint профиля ${profileId} на порту ${port} не стал доступен`);
    }
    throwIfOperationAborted(signal);
    const cdpUrl = `http://127.0.0.1:${port}`;
    const connection = chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
    if (!signal) {
      return connection;
    }
    return new Promise<Browser>((resolve, reject) => {
      const onAbort = () => {
        signal.removeEventListener('abort', onAbort);
        reject(new Error('Browser lifecycle operation cancelled'));
      };
      if (signal.aborted) {
        onAbort();
      } else {
        signal.addEventListener('abort', onAbort, { once: true });
      }
      void connection.then(
        (browser) => {
          signal.removeEventListener('abort', onAbort);
          if (signal.aborted) {
            browser.removeAllListeners();
            return;
          }
          resolve(browser);
        },
        (error) => {
          signal.removeEventListener('abort', onAbort);
          if (!signal.aborted) {
            reject(error);
          }
        },
      );
    });
  }

  private async restartProfileForMissingCdp(
    visionClient: VisionClient,
    folderId: string,
    profileId: string,
    signal?: AbortSignal,
  ): Promise<{ port: number | null }> {
    try {
      return await visionClient.restartProfileToRecoverPort(folderId, profileId, {
        stopTimeoutSec: RECOVERY_STOP_TIMEOUT_SECONDS,
        portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
        settleAfterStopMs: RECOVERY_SETTLE_DELAY_MS,
        signal,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Не удалось восстановить CDP-порт автоперезапуском профиля ${profileId}: ${message}`,
      );
    }
  }
}

async function createPageWithinOperation(
  context: BrowserContext,
  signal?: AbortSignal,
): Promise<Page> {
  throwIfOperationAborted(signal);
  const pagePromise = context.newPage();
  try {
    const page = await raceWithAbort(pagePromise, signal);
    throwIfOperationAborted(signal);
    return page;
  } catch (error) {
    if (signal?.aborted) {
      // Playwright cannot cancel BrowserContext.newPage(). If transport
      // cancellation wins the race, close the eventually-created page so an
      // unowned tab cannot survive and later be mistaken for a role page.
      void pagePromise
        .then(async (page) => {
          if (!isPageClosed(page)) {
            await page.close({ runBeforeUnload: false });
          }
        })
        .catch(() => undefined);
    }
    throw error;
  }
}

function throwIfOperationAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new Error('Browser lifecycle operation cancelled');
  }
}

async function navigatePageWithinOperation(
  page: Page,
  targetUrl: string,
  signal?: AbortSignal,
): Promise<void> {
  throwIfOperationAborted(signal);
  const onAbort = (): void => {
    void page.close({ runBeforeUnload: false }).catch(() => undefined);
  };
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    await raceWithAbort(
      page.goto(targetUrl, { waitUntil: 'domcontentloaded' }),
      signal,
    );
    throwIfOperationAborted(signal);
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

async function reloadPageWithinOperation(
  page: Page,
  signal?: AbortSignal,
): Promise<void> {
  throwIfOperationAborted(signal);
  const onAbort = (): void => {
    // Playwright cannot cancel page.reload directly. Closing the isolated role
    // page terminates the navigation and guarantees no browser work survives
    // the fenced gRPC request.
    void page.close({ runBeforeUnload: false }).catch(() => undefined);
  };
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    await raceWithAbort(
      page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 }),
      signal,
    );
    throwIfOperationAborted(signal);
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

function buildMaintenanceRecoveryRequiredError(profileId: string): Error {
  return new Error(
    `Профиль ${profileId} не имеет готового CDP-порта. `
    + 'Требуется capability-authorized maintenance recovery.',
  );
}
