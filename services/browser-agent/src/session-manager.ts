import {
  chromium,
  type Browser,
  type BrowserContext,
  type Page,
} from "playwright";
import { v4 as uuidv4 } from "uuid";
import { VisionClient } from "./vision-client.js";
import { STEALTH_INIT_SCRIPT } from "./stealth.js";
import { generateHumanProfile } from "./humanizer.js";
import {
  adsManagerColumnsQs,
  adsManagerUrlUsesColumnsQs,
} from "./am/am-columns-preset.js";
import { raceWithAbort } from "./in-page-abort.js";
import { withPageRoleLockForSession } from "./page-lock.js";
import { pageHasMetaApiToken } from "./meta-api/client.js";
import { tracePageNav } from "./trace.js";
import type { BrowserPageRole, BrowserSession, HumanProfile } from "./types.js";

const EXISTING_PROFILE_PORT_GRACE_SECONDS = 8;
const START_PROFILE_PORT_WAIT_SECONDS = 20;
const CDP_READY_WAIT_SECONDS = 20;
const RECOVERY_STOP_TIMEOUT_SECONDS = 20;
const RECOVERY_SETTLE_DELAY_MS = 1_000;
// Разлогиненный профиль чинит человек. Пока защёлка держит паузу, вкладки не
// создаются и не навигируются: повторная попытка не вернёт сессию Facebook, она
// только откроет и закроет ещё одну вкладку. Раз в этот интервал — одна проба.
const LOGIN_REQUIRED_RETRY_COOLDOWN_MS = 5 * 60_000;
// Проба готовности канала стучится раз в две секунды. Пока открытие вкладки
// кабинета падает по одной и той же причине, каждая такая проба создаёт вкладку и
// закрывает её. Первый отказ повторяем сразу — он может быть блипом; дальше
// придерживаем, иначе отказ превращается в бесконечный цикл вкладок.
const OPEN_FAILURE_BACKOFF_MS = [0, 15_000, 60_000];

export function isAdsManagerUrl(url: string | null | undefined): boolean {
  try {
    const parsed = new URL(String(url || ""));
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
      return false;
    }
    const hostname = parsed.hostname.toLowerCase();
    const pathname = parsed.pathname.toLowerCase();

    // Проверяем только фактический origin/path вкладки. Поиск подстроки во всём URL
    // ошибочно принимал business/loginpage за Ads Manager, когда его query-параметр
    // next= содержал закодированный adsmanager.facebook.com. В результате Meta API
    // читал DOM страницы входа и ложно сообщал token_not_found при живом кабинете.
    const hasAdsManagerPath =
      pathname === "/adsmanager" || pathname.startsWith("/adsmanager/");
    const isFacebookHost =
      hostname === "facebook.com" || hostname.endsWith(".facebook.com");
    return isFacebookHost && hasAdsManagerPath;
  } catch {
    return false;
  }
}

/** Facebook увёл навигацию на вход/чекпоинт — профиль разлогинен.
 *
 * Отличать это от «кабинет не подтверждён» обязательно: не подтверждённый act —
 * ошибка адресации, которую чинит следующая попытка, а страница входа означает,
 * что попыток может быть сколько угодно и ни одна не сработает. */
export function isFacebookLoginUrl(url: string | null | undefined): boolean {
  try {
    const parsed = new URL(String(url || ""));
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
      return false;
    }
    const hostname = parsed.hostname.toLowerCase();
    const isFacebookHost =
      hostname === "facebook.com" || hostname.endsWith(".facebook.com");
    if (!isFacebookHost) {
      return false;
    }
    const pathname = parsed.pathname.toLowerCase().replace(/\/+$/, "");
    return (
      pathname === "/login" ||
      pathname === "/login.php" ||
      pathname.startsWith("/login/") ||
      pathname === "/checkpoint" ||
      pathname.startsWith("/checkpoint/") ||
      pathname === "/business/loginpage" ||
      pathname.startsWith("/business/loginpage/")
    );
  } catch {
    return false;
  }
}

/** Достаёт numeric ad-account id из URL Ads Manager (?act=<num>). null, если не читается. */
export function extractAdAccountId(
  url: string | null | undefined,
): string | null {
  try {
    const actValues = new URL(String(url || "")).searchParams.getAll("act");
    if (actValues.length !== 1) {
      return null;
    }
    return /^\d{1,32}$/.test(actValues[0]) ? actValues[0] : null;
  } catch {
    return null;
  }
}

function safePageUrl(page: Page | null | undefined): string {
  try {
    return page?.url?.() || "";
  } catch {
    return "";
  }
}

/** Живо ли CDP-соединение. Клиент без isConnected считается живым (как в тестах). */
function isBrowserAlive(browser: Browser | null | undefined): boolean {
  try {
    return Boolean(
      browser &&
        (typeof browser.isConnected !== "function" || browser.isConnected()),
    );
  } catch {
    return false;
  }
}

function safeBrowserContexts(browser: Browser | null): BrowserContext[] {
  try {
    return browser?.contexts() || [];
  } catch {
    return [];
  }
}

function safeContextPages(context: BrowserContext): Page[] {
  try {
    return context.pages();
  } catch {
    return [];
  }
}

const SAFE_CABINET_TAB_ERRORS = [
  /^cabinet_not_found: Основная страница браузера недоступна$/,
  /^cabinet_not_found: Основная страница браузера недоступна: (?:scan|control|interactive) кабинет не определён$/,
  /^cabinet_not_found: ad account id must be 1\.\.32 digits$/,
  /^cabinet_not_found: (?:could not create page|navigation failed) for act=\d+$/,
  /^cabinet_not_confirmed: (?:final Ads Manager URL does not confirm|selected page does not confirm) act=\d+$/,
  /^cabinet_login_required: Vision profile is signed out \(act=\d+\)$/,
  /^cabinet_backoff: repeated failures opening act=\d+, retry is held$/,
];

/** Return an incident-safe OpenCabinetTabs error without raw browser details. */
export function safeCabinetTabError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || "");
  return SAFE_CABINET_TAB_ERRORS.some((pattern) => pattern.test(message))
    ? message
    : "cabinet_not_confirmed: cabinet tab unavailable";
}

/** URL Ads Manager для конкретного кабинета (мульти-кабинет, act без префикса act_).
 * Уровень КАМПАНИЙ + набор колонок пользователя (am-columns-preset) — пользователь
 * сразу видит нужные метрики; на скан (am_tabular level=ad через fetch) уровень
 * вкладки не влияет. */
export function adsManagerUrlForAct(
  actId: string,
  amColumnsQs?: string | null,
): string {
  if (!/^\d{1,32}$/.test(actId)) {
    throw new Error("cabinet_not_found: ad account id must be 1..32 digits");
  }
  return (
    `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${actId}` +
    `&${adsManagerColumnsQs(amColumnsQs)}`
  );
}

/** Collapse a live page URL to the only URL shape safe to persist or expose. */
export function canonicalAdsManagerUrl(
  url: string | null | undefined,
  amColumnsQs?: string | null,
): string | null {
  const actId = extractAdAccountId(url);
  return actId && isAdsManagerUrl(url)
    ? adsManagerUrlForAct(actId, amColumnsQs)
    : null;
}

/**
 * Страницы, созданные агентом под money-роль. Реестр процессный, а не
 * сессионный: множество чужих ролей строилось только из карт ТЕКУЩЕЙ сессии, а
 * поиск шёл по всему браузеру — вкладка, зарегистрированная сессией observer'а,
 * свободно становилась money-страницей в сессии залива, и её перезагрузка
 * сканом убивала execution context идущей мутации. Инвариант «страница скана не
 * равна control-странице» без общего реестра межпроцессно не выполняется.
 */
const _agentMoneyPages = new WeakSet<Page>();

/**
 * Подмножество: страницы роли control — те, на которых идёт необратимая
 * мутация под control page-lock. Отделены от остального реестра намеренно.
 * Роль interactive тоже не отдаётся чужому скану, но наблюдать её можно и
 * нужно: именно её создаёт лечащая ручка ensure-cdp, и именно её видит
 * релизный предикат, читающий канал без кабинета.
 */
const _agentControlPages = new WeakSet<Page>();

/** Пометить страницу как владение агента под ролью. Синхронно, до навигации. */
function reserveAgentRolePage(page: Page, role: BrowserPageRole): void {
  if (role === "scan") {
    return;
  }
  _agentMoneyPages.add(page);
  if (role === "control") {
    _agentControlPages.add(page);
  }
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
  for (const context of safeBrowserContexts(browser)) {
    for (const page of safeContextPages(context)) {
      if (typeof page.isClosed === "function" && page.isClosed()) {
        continue;
      }
      if (excludedPages.has(page) || _agentMoneyPages.has(page)) {
        continue;
      }
      if (isConfirmedAdsManagerPage(page, actId)) {
        return page;
      }
    }
  }
  return null;
}

function isConfirmedAdsManagerPage(
  page: Page | null | undefined,
  actId: string,
): page is Page {
  if (!page || isPageClosed(page)) {
    return false;
  }
  try {
    const url = safePageUrl(page);
    return isAdsManagerUrl(url) && extractAdAccountId(url) === actId;
  } catch {
    return false;
  }
}

function isPageClosed(page: Page): boolean {
  return typeof page.isClosed === "function" && page.isClosed();
}

export function findPreferredPrimaryPage(browser: Browser | null): Page | null {
  if (!browser) {
    return null;
  }

  let fallbackPage: Page | null = null;
  for (const context of safeBrowserContexts(browser)) {
    for (const page of safeContextPages(context)) {
      if (isPageClosed(page)) {
        continue;
      }
      fallbackPage = fallbackPage || page;
      if (isAdsManagerUrl(safePageUrl(page))) {
        return page;
      }
    }
  }

  return fallbackPage;
}

/** Живая вкладка Ads Manager без побочных эффектов: ничего не создаёт и не навигирует.
 *
 * Отличие от findPreferredPrimaryPage: здесь нет отката на первую попавшуюся
 * вкладку. Проба здоровья без явно названного кабинета должна честно ответить
 * «нет страницы», а не выдать за Ads Manager чужую вкладку оператора.
 *
 * Не отдаётся ровно одно: control-страница. Проба готовности ходит раз в две
 * секунды и под СВОИМ page-lock — взяв control-страницу, она читала бы DOM
 * вкладки, которая в этот момент несёт необратимую мутацию под другим замком.
 *
 * Роль interactive здесь остаётся видимой намеренно. Наблюдателей без кабинета
 * четверо: проба готовности, полная проба канала в watchdog, чтение
 * `GET /api/settings/vision`, на котором стоит релизный предикат, и
 * диагностический health-probe-cli. Всем, кроме пробы готовности, показывать
 * нечего, кроме вкладки, созданной лечащей ручкой ensure-cdp, — а она
 * создаётся именно под ролью interactive.
 */
export function findLiveAdsManagerPage(browser: Browser | null): Page | null {
  if (!browser) {
    return null;
  }

  for (const context of safeBrowserContexts(browser)) {
    for (const page of safeContextPages(context)) {
      if (isPageClosed(page) || _agentControlPages.has(page)) {
        continue;
      }
      if (isAdsManagerUrl(safePageUrl(page))) {
        return page;
      }
    }
  }

  return null;
}

/** Запоминает URL живой вкладки Ads Manager на сессии — чтобы переоткрыть её при self-heal. */
export function rememberAdsManagerUrl(
  session: BrowserSession,
  page: Page | null | undefined,
): void {
  try {
    const canonicalUrl = canonicalAdsManagerUrl(page?.url?.());
    if (canonicalUrl) {
      session.lastAdsManagerUrl = canonicalUrl;
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
  /**
   * Профили, у которых Facebook увёл навигацию на вход. Ключ — Vision-профиль,
   * а не сессия: одна физическая вкладка переживает пересоздание gRPC-сессии, и
   * защёлка, живущая в сессии, снималась бы вместе с ней — вкладка снова начала
   * бы открываться и закрываться по кругу.
   *
   * ``page`` — удержанная страница входа. Её не закрываем: подключившись к
   * рабочему столу, оператор входит прямо в ней, а следующая проба переиспользует
   * ту же вкладку вместо новой.
   */
  private loginRequired = new Map<string, { at: number; page: Page | null }>();
  /**
   * Подряд идущие отказы открытия вкладки конкретного кабинета. Ключ —
   * профиль и кабинет: разлогин общий для профиля, а «кабинет не подтверждён»
   * или сетевой отказ относятся к одному кабинету и не должны придерживать
   * остальные.
   */
  private openFailures = new Map<string, { streak: number; until: number }>();

  /**
   * targetId CDP-вкладки → роль и кабинет. targetId стабилен в пределах жизни
   * браузера, переживает переподключение CDP и не зависит от идентичности
   * объекта Page. Карта используется при reconnect для восстановления реестров
   * ролей вместо их обнуления: без неё новый Page-прокси той же физической
   * вкладки не числился в _agentMoneyPages и мог быть усыновлён ролью scan.
   */
  private pageRolesByTargetId = new Map<
    string,
    { role: BrowserPageRole; actId: string }
  >();

  // Профили, которые мы видели ЗАПУЩЕННЫМИ в этом процессе, и профили, которые
  // мы останавливали САМИ. Пара нужна, чтобы отличить холодный старт (профиль
  // просто не запущен — запускаем спокойно) от «профиль забрала другая машина»
  // (мы его видели живым, сами не останавливали, а из /list он исчез).
  // Во втором случае запускать нельзя: Vision развернёт архив из облака, то есть
  // снимок чужой машины, и Facebook погасит такую сессию как чужую.
  private observedRunningProfiles = new Set<string>();
  private selfStoppedProfiles = new Set<string>();
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

    // Один профиль Vision — одна сессия. Реестр вкладок принадлежит сессии, и
    // каждая новая сессия открывала СВОИ вкладки тех же кабинетов: перезапуск
    // воркера или второй потребитель канала удваивали набор вкладок профиля, а
    // прежние оставались в браузере навсегда. Профиль один и физически, и здесь.
    const liveSession = forceProfileRestart
      ? null
      : this.findLiveSessionForProfile(visionProfileId);
    if (liveSession) {
      console.log(
        `[session-manager] startBrowser: профиль ${visionProfileId} уже ведёт ` +
          `сессия ${liveSession.id}, переиспользую её вкладки`,
      );
      // Свежие реквизиты Vision у вызывающего новее наших: перезапуск профиля
      // под maintenance пойдёт по ним, а не по токену, протухшему с прошлой
      // сессии.
      if (visionXToken) {
        liveSession.visionXToken = visionXToken;
      }
      if (visionApiUrl) {
        liveSession.visionApiUrl = visionApiUrl;
      }
      return liveSession;
    }

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Vision API иногда требует folder_id отдельно, поэтому восстанавливаем его по profile_id.
    let folderId = visionFolderId;
    if (!folderId) {
      folderId = await visionClient.resolveFolderId(visionProfileId, signal);
    }

    console.log(
      `[session-manager] startBrowser: profile=${visionProfileId} folder=${folderId}`,
    );
    const existingProfile = await visionClient.getProfile(
      visionProfileId,
      signal,
    );
    console.log(
      `[session-manager] /list для ${visionProfileId}: ${
        existingProfile ? `port=${existingProfile.port}` : "НЕТ в списке"
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
      console.log(
        `[session-manager] профиль уже с CDP-портом ${existingProfile.port}, использую как есть`,
      );
      this.observedRunningProfiles.add(visionProfileId);
      this.selfStoppedProfiles.delete(visionProfileId);
      profile = { port: existingProfile.port };
    } else if (existingProfile) {
      // У Vision порт иногда появляется с задержкой, поэтому сначала даем ему короткий grace period.
      console.log(
        `[session-manager] профиль без CDP, жду до ${EXISTING_PROFILE_PORT_GRACE_SECONDS}с`,
      );
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
    } else if (
      forceProfileRestart
      && this.observedRunningProfiles.has(visionProfileId)
      && !this.selfStoppedProfiles.has(visionProfileId)
    ) {
      // Профиль был живым в этом процессе, мы его не останавливали — и он исчез
      // из /list. Значит его забрал другой потребитель (вторая машина с той же
      // папкой профилей). Запуск здесь развернул бы облачный архив поверх живой
      // сессии и разлогинил бы кабинет: ровно та цепочка, которая на проде
      // выглядела как «профиль разлогинен» после каждого восстановления.
      throw buildProfileTakenElsewhereError(visionProfileId);
    } else if (forceProfileRestart) {
      console.log(
        `[session-manager] maintenance recovery starts stopped profile`,
      );
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
      status: "connected",
    };

    this.sessions.set(session.id, session);
    return session;
  }

  async recoverBrowserProfileUnderMaintenance(
    options: {
      visionXToken: string;
      visionApiUrl: string;
      visionProfileId: string;
      visionFolderId?: string;
    },
    signal?: AbortSignal,
  ): Promise<BrowserSession> {
    throwIfOperationAborted(signal);
    const matchingSessions = Array.from(this.sessions.values())
      .filter((session) => session.visionProfileId === options.visionProfileId)
      .sort(
        (left, right) =>
          right.connectedAt.getTime() - left.connectedAt.getTime(),
      );
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

  async reconnectBrowser(
    sessionId: string,
    options?: {
      signal?: AbortSignal;
    },
  ): Promise<BrowserSession> {
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
    const previousLatchKey = this.loginLatchKey(session);
    // Старый CDP-клиент — отвяжем его ПОСЛЕ успешного нового подключения (H-6/BA-2),
    // чтобы не копить ws-соединения и listeners под recovery-нагрузкой.
    const oldBrowser = session.browser;

    const visionXToken = options.visionXToken;
    const visionApiUrl = options.visionApiUrl;
    const visionProfileId = options.visionProfileId;

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Переподключение сначала пытается забрать уже существующий CDP-порт и не трогать окно профиля.
    const existingProfile = await visionClient.getProfile(
      visionProfileId,
      signal,
    );
    const resolvedFolderId =
      session.visionProfileId === visionProfileId
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
    // Page-прокси прежнего CDP-соединения мертвы. Восстанавливаем реестры
    // ролей по targetId: тот же targetId в новом соединении возвращает новый
    // прокси той же физической вкладки. Если targetId получить не удалось —
    // реестр остаётся пустым (деградация, не усыновление).
    const restoredPages = await this.restoreRolePagesAfterReconnect(browser);
    session.scanPages = restoredPages.scanPages;
    session.controlPages = restoredPages.controlPages;
    session.interactivePages = restoredPages.interactivePages;
    session.playwright = chromium;
    session.cdpPort = resolvedPort;
    session.status = "connected";
    session.connectedAt = new Date();
    session.visionXToken = visionXToken;
    session.visionApiUrl = visionApiUrl;
    session.visionProfileId = visionProfileId;
    session.visionFolderId = resolvedFolderId;
    // Переподключение — явное действие человека или maintenance: он мог войти
    // заново. Страницы прежнего CDP-соединения всё равно мертвы, держать по ним
    // паузу нечем — снимаем и проверяем вход первой же операцией.
    this.loginRequired.delete(previousLatchKey);
    this.clearLoginRequired(session);

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
    opts: {
      role: BrowserPageRole;
      actId?: string;
      page?: Page;
      signal?: AbortSignal;
    },
  ): Promise<{ action: string; ok: boolean }> {
    const session = this.getSession(sessionId);
    return withPageRoleLockForSession(session, opts.role, opts.actId, () =>
      this.reloadPageAfterNetworkFailureWithinRoleLock(sessionId, opts),
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
    opts: {
      role: BrowserPageRole;
      actId?: string;
      page?: Page;
      signal?: AbortSignal;
    },
  ): Promise<{ action: string; ok: boolean }> {
    const session = this.getSession(sessionId);
    let ok = true;
    const action = "reload";
    try {
      const page = opts.page;
      const closed = typeof page?.isClosed === "function" && page.isClosed();
      if (page && !closed) {
        await reloadPageWithinOperation(
          page,
          {
            session: sessionId,
            role: opts.role,
            act: opts.actId ?? "",
            by: "heal",
          },
          opts.signal,
        );
      }
    } catch (err) {
      if (opts.signal?.aborted) {
        throw err;
      }
      ok = false;
      // Текст исключения сюда не попадает: в нём встречается содержимое
      // страницы. Класса отказа хватает, чтобы отличить закрытую вкладку от
      // мёртвого CDP, а подробности живут в записи навигации выше.
      console.error(
        `[heal] session=${sessionId} page reload failed: ${
          err instanceof Error ? err.name : "unknown"
        }`,
      );
    }
    session.lastHealAt = new Date();
    session.netFailureStreak = 0;
    console.warn(`[heal] session=${sessionId} action=${action} ok=${ok}`);
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
      .filter((session) => session.status === "connected" && session.browser)
      .sort(
        (left, right) =>
          right.connectedAt.getTime() - left.connectedAt.getTime(),
      );

    const adsSession = sessions.find((session) => {
      const preferredPage = findPreferredPrimaryPage(session.browser);
      return preferredPage ? isAdsManagerUrl(preferredPage.url()) : false;
    });
    const session = adsSession || sessions[0];
    if (!session) {
      throw new Error("Активная browser-agent сессия не найдена");
    }
    return session;
  }

  /** Сессия, которая уже ведёт этот профиль по живому CDP-соединению. */
  private findLiveSessionForProfile(profileId: string): BrowserSession | null {
    const normalizedProfileId = String(profileId || "").trim();
    if (!normalizedProfileId) {
      return null;
    }
    return (
      Array.from(this.sessions.values())
        .filter(
          (candidate) =>
            candidate.status === "connected" &&
            candidate.visionProfileId === normalizedProfileId &&
            isBrowserAlive(candidate.browser),
        )
        .sort(
          (left, right) =>
            right.connectedAt.getTime() - left.connectedAt.getTime(),
        )[0] ?? null
    );
  }

  getSessionForVisionProfile(profileId: string): BrowserSession {
    const normalizedProfileId = String(profileId || "").trim();
    if (!normalizedProfileId) {
      throw new Error("Canonical Vision profile id is required");
    }
    const session = Array.from(this.sessions.values())
      .filter(
        (candidate) =>
          candidate.status === "connected" &&
          candidate.browser &&
          candidate.visionProfileId === normalizedProfileId,
      )
      .sort(
        (left, right) =>
          right.connectedAt.getTime() - left.connectedAt.getTime(),
      )[0];
    if (!session) {
      throw new Error(
        `Active session for Vision profile ${normalizedProfileId} not found`,
      );
    }
    return session;
  }

  /** Return the dedicated scan page for one cabinet. */
  async ensureScanPage(
    session: BrowserSession,
    opts: {
      fallbackUrl?: string;
      actId?: string;
      amColumnsQs?: string;
      signal?: AbortSignal;
    } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, "scan", opts);
  }

  /** Return the dedicated control/Meta-mutation page for one cabinet. */
  async ensureControlPage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, "control", opts);
  }

  /** Return the dedicated non-money Graph/media page for one cabinet. */
  async ensureInteractivePage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string; signal?: AbortSignal } = {},
  ): Promise<Page> {
    return this.ensureRolePage(session, "interactive", opts);
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
    const cabinetKey =
      String(actId || "")
        .replace(/^act_/, "")
        .trim() || "__default__";
    const rolePages: Record<BrowserPageRole, Map<string, Page>> = {
      scan: session.scanPages,
      control: session.controlPages,
      interactive: session.interactivePages,
    };
    if (rolePages[role]?.get(cabinetKey) === page) {
      rolePages[role].delete(cabinetKey);
    }
    if (role === "scan" && session.primaryPage === page) {
      session.primaryPage = null;
    }
    this.poisonedPages.add(page);
    void page.close().catch(() => undefined);
  }

  private loginLatchKey(session: BrowserSession): string {
    return String(session.visionProfileId || "").trim() || session.id;
  }

  /** Сколько ещё держим паузу по разлогину. 0 — пауза истекла или её не было. */
  private loginRequiredRemainingMs(session: BrowserSession, now: number): number {
    const latch = this.loginRequired.get(this.loginLatchKey(session));
    if (!latch) {
      return 0;
    }
    return Math.max(0, LOGIN_REQUIRED_RETRY_COOLDOWN_MS - (now - latch.at));
  }

  /** Забрать удержанную страницу входа для повторной пробы (одна вкладка на профиль).
   *
   * Пока в ней открыт вход, страница не отдаётся никому: в этот момент в ней
   * работает человек. Навигировать её на кабинет значило бы стирать наполовину
   * введённые логин и код — ровно то, из-за чего войти и не удавалось. */
  private takeRetainedLoginPage(session: BrowserSession): Page | null {
    const key = this.loginLatchKey(session);
    const latch = this.loginRequired.get(key);
    const page = latch?.page ?? null;
    if (!page || isPageClosed(page) || this.poisonedPages.has(page)) {
      if (latch) {
        this.loginRequired.set(key, { at: latch.at, page: null });
      }
      return null;
    }
    if (isFacebookLoginUrl(safePageUrl(page))) {
      return null;
    }
    // Адрес сменился — вход состоялся или страницу увели вручную. Забираем.
    this.loginRequired.set(key, { at: latch!.at, page: null });
    return page;
  }

  /**
   * Держать ли паузу разлогина прямо сейчас.
   *
   * Удержанная вкладка входа — не только вежливость к оператору, но и самый
   * точный признак состояния профиля:
   *   в ней всё ещё вход  → человек логинится, паузу продлеваем и браузер не
   *                         трогаем вообще, сколько бы это ни заняло;
   *   адрес сменился      → вход состоялся, ждать конца интервала незачем;
   *   вкладки нет         → судим по интервалу, как обычно.
   */
  private shouldHoldForLogin(session: BrowserSession): boolean {
    const key = this.loginLatchKey(session);
    const latch = this.loginRequired.get(key);
    if (!latch) {
      return false;
    }
    const page = latch.page;
    if (page && !isPageClosed(page)) {
      if (isFacebookLoginUrl(safePageUrl(page))) {
        this.loginRequired.set(key, { at: Date.now(), page });
        return true;
      }
      return false;
    }
    return this.loginRequiredRemainingMs(session, Date.now()) > 0;
  }

  private markLoginRequired(session: BrowserSession, page: Page | null): void {
    this.loginRequired.set(this.loginLatchKey(session), {
      at: Date.now(),
      page: page && !isPageClosed(page) ? page : null,
    });
  }

  /** Вход состоялся: снимаем паузу, удержанную страницу больше не помним. */
  private clearLoginRequired(session: BrowserSession): void {
    this.loginRequired.delete(this.loginLatchKey(session));
  }

  private openFailureKey(session: BrowserSession, actId: string): string {
    return `${this.loginLatchKey(session)}:${actId}`;
  }

  private recordOpenFailure(session: BrowserSession, actId: string): number {
    const key = this.openFailureKey(session, actId);
    const streak = (this.openFailures.get(key)?.streak ?? 0) + 1;
    const backoff =
      OPEN_FAILURE_BACKOFF_MS[
        Math.min(streak, OPEN_FAILURE_BACKOFF_MS.length) - 1
      ];
    this.openFailures.set(key, { streak, until: Date.now() + backoff });
    return backoff;
  }

  /**
   * Единственный выход из неудавшейся навигации role-страницы.
   *
   * Страница входа обрабатывается отдельно от остальных отказов: вкладка
   * остаётся жить, взводится защёлка, а причина названа своим именем. Всё
   * прочее — прежнее поведение: карантин, закрытие своей вкладки, безопасный
   * текст без URL и токенов.
   */
  private failRolePageNavigation(
    session: BrowserSession,
    opts: {
      page: Page;
      resolvedAct: string;
      ownPages: Map<string, Page>;
      cabinetKey: string;
      error: unknown;
      signal?: AbortSignal;
    },
  ): never {
    const { page, resolvedAct, ownPages, cabinetKey, error } = opts;
    ownPages.delete(cabinetKey);
    if (opts.signal?.aborted) {
      this.poisonedPages.add(page);
      if (!isPageClosed(page)) {
        void page.close({ runBeforeUnload: false }).catch(() => undefined);
      }
      throw new Error("Browser operation cancelled");
    }
    if (isFacebookLoginUrl(safePageUrl(page))) {
      this.markLoginRequired(session, page);
      console.warn(
        `[cabinet] act=${resolvedAct} профиль разлогинен: вкладка входа оставлена, ` +
          `пауза ${LOGIN_REQUIRED_RETRY_COOLDOWN_MS / 1000}s`,
      );
      throw new Error(
        `cabinet_login_required: Vision profile is signed out (act=${resolvedAct})`,
      );
    }
    this.poisonedPages.add(page);
    if (!isPageClosed(page)) {
      void page.close({ runBeforeUnload: false }).catch(() => undefined);
    }
    const backoffMs = this.recordOpenFailure(session, resolvedAct);
    const reason =
      error instanceof Error && error.message.startsWith("cabinet_")
        ? error.message.split(":")[0]
        : "navigation_failed";
    console.warn(
      `[cabinet] act=${resolvedAct} вкладку открыть не удалось (${reason}), ` +
        `следующая попытка не раньше чем через ${backoffMs / 1000}s`,
    );
    if (error instanceof Error && error.message.startsWith("cabinet_")) {
      throw error;
    }
    throw new Error(
      `cabinet_not_found: navigation failed for act=${resolvedAct}`,
    );
  }

  private async ensureRolePage(
    session: BrowserSession,
    role: BrowserPageRole,
    opts: {
      fallbackUrl?: string;
      actId?: string;
      amColumnsQs?: string;
      signal?: AbortSignal;
    },
  ): Promise<Page> {
    throwIfOperationAborted(opts.signal);
    session.scanPages ??= new Map();
    session.controlPages ??= new Map();
    session.interactivePages ??= new Map();

    const browser = session.browser;
    const contexts = isBrowserAlive(browser) ? safeBrowserContexts(browser) : [];
    const context = contexts[0];
    if (!browser || !context) {
      throw new Error(
        "cabinet_not_found: Основная страница браузера недоступна",
      );
    }

    const explicitAct = opts.actId == null ? "" : String(opts.actId);
    if (explicitAct && !/^\d{1,32}$/.test(explicitAct)) {
      throw new Error("cabinet_not_found: ad account id must be 1..32 digits");
    }
    const preferredPage = findPreferredPrimaryPage(browser);
    const primaryUrl = safePageUrl(session.primaryPage);
    const preferredUrl = safePageUrl(preferredPage);
    const sourceUrl =
      opts.fallbackUrl ||
      session.lastAdsManagerUrl ||
      (isAdsManagerUrl(primaryUrl) ? primaryUrl : undefined) ||
      (isAdsManagerUrl(preferredUrl) ? preferredUrl : undefined);
    const resolvedAct = explicitAct || extractAdAccountId(sourceUrl) || "";
    if (!resolvedAct) {
      throw new Error(
        `cabinet_not_found: Основная страница браузера недоступна: ${role} кабинет не определён`,
      );
    }
    const cabinetKey = resolvedAct;
    // Профиль разлогинен: не трогаем браузер вообще. Ни новой вкладки, ни
    // навигации, ни закрытия — иначе каждая попытка вызывающего превращается в
    // ещё один цикл «открыл вкладку → Facebook отдал вход → закрыл вкладку».
    if (this.shouldHoldForLogin(session)) {
      throw new Error(
        `cabinet_login_required: Vision profile is signed out (act=${resolvedAct})`,
      );
    }
    const sourceMatchesAct =
      resolvedAct &&
      isAdsManagerUrl(sourceUrl) &&
      extractAdAccountId(sourceUrl) === resolvedAct;
    const targetUrl = sourceMatchesAct
      ? canonicalAdsManagerUrl(sourceUrl, opts.amColumnsQs)
      : adsManagerUrlForAct(resolvedAct, opts.amColumnsQs);
    if (!targetUrl || !isAdsManagerUrl(targetUrl)) {
      throw new Error(
        `cabinet_not_found: Основная страница браузера недоступна: ${role} кабинет не определён`,
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
    // Вкладка этой роли и этого кабинета, если она у нас уже есть. Единственная
    // причина её не брать — вкладки больше нет (закрыта или в карантине) или её
    // держит другая роль. Расхождение адреса, чужой набор колонок и умершая
    // внутри страницы сессия чинятся обновлением ТОЙ ЖЕ вкладки: каждая
    // дополнительная вкладка кабинета — ещё один контекст, который может умереть
    // посреди необратимой операции, и по набору вкладок больше не видно, где
    // идёт залив.
    const mapped = ownPages.get(cabinetKey);
    let page: Page | null =
      mapped &&
      !isPageClosed(mapped) &&
      !this.poisonedPages.has(mapped) &&
      !opposite.has(mapped)
        ? mapped
        : null;
    if (!page) {
      ownPages.delete(cabinetKey);
    }

    // A page assigned to the opposite role is never eligible. Pages assigned
    // to another cabinet of the same role are excluded as well.
    const reserved = new Set<Page>(opposite);
    // WeakSet is intentionally non-enumerable. Materialize only the currently
    // live context pages for this selection call; the temporary Set disappears
    // after the call and cannot retain abandoned page graphs globally.
    for (const candidateContext of contexts) {
      for (const candidate of safeContextPages(candidateContext)) {
        if (this.poisonedPages.has(candidate)) {
          reserved.add(candidate);
        }
      }
    }
    for (const [key, otherCabinetPage] of ownPages) {
      if (key !== cabinetKey) reserved.add(otherCabinetPage);
    }

    // Money-роли НИКОГДА не усыновляют чужую вкладку: единственным критерием
    // подбора был URL, а маркера «вкладка агента» не существовало — поэтому
    // ручная вкладка оператора или страница скана другой сессии становились
    // страницей денежной мутации. Роль scan усыновление сохраняет осознанно:
    // это не money-путь, и переоткрывать вкладку кабинета на каждый скан дорого.
    if (role === "scan") {
      page ??= resolvedAct
        ? findAdsManagerPageByAct(browser, resolvedAct, reserved)
        : null;
    }

    let createdNow = false;
    if (!page) {
      // Живой вкладки нет, значит сейчас будет создание и навигация — именно то,
      // что превращается в цикл «открыл → закрыл», если кабинет падает подряд.
      // Подтверждённая вкладка сюда не доходит: она возвращается без создания.
      const heldUntil = this.openFailures.get(
        this.openFailureKey(session, resolvedAct),
      )?.until;
      if (heldUntil !== undefined && Date.now() < heldUntil) {
        throw new Error(
          `cabinet_backoff: repeated failures opening act=${resolvedAct}, retry is held`,
        );
      }
      // Проба после паузы переиспользует удержанную страницу входа: если человек
      // уже вошёл в ней, там теперь живая сессия, а вкладка остаётся одна.
      page = this.takeRetainedLoginPage(session);
      if (page) {
        reserveAgentRolePage(page, role);
      }
      if (!page) {
        try {
          page = await createPageWithinOperation(context, opts.signal);
          // Резервируем ДО навигации и до любого следующего await: иначе между
          // созданием и регистрацией страницу успевает выбрать чужая сессия.
          reserveAgentRolePage(page, role);
        } catch {
          if (opts.signal?.aborted) {
            throw new Error("Browser operation cancelled");
          }
          throw new Error(
            `cabinet_not_found: could not create page for act=${resolvedAct}`,
          );
        }
      }
      createdNow = true;
    }

    // Обновление вкладки — единственный способ починить её состояние. Новая
    // вкладка вместо обновления оставляла прежнюю открытой навсегда: так один
    // кабинет и набирал по три-четыре копии.
    const needsNavigation =
      createdNow ||
      !isConfirmedAdsManagerPage(page, resolvedAct) ||
      (opts.amColumnsQs !== undefined &&
        !adsManagerUrlUsesColumnsQs(safePageUrl(page), opts.amColumnsQs)) ||
      // Money-роль не может судить о живой сессии по одному URL: control-страница
      // умирает без видимой навигации, а isConfirmedAdsManagerPage сравнивает
      // только pathname и act. Тот же признак, что использует реальная проба
      // (checkMetaApiHealth), решает, отдавать страницу под мутацию или сначала
      // обновить её.
      (role === "control" && !(await pageHasMetaApiToken(page, opts.signal)));
    if (needsNavigation) {
      try {
        await navigatePageWithinOperation(
          page,
          targetUrl,
          {
            session: session.id,
            role,
            act: resolvedAct,
            by: "operation",
          },
          opts.signal,
        );
        if (!isConfirmedAdsManagerPage(page, resolvedAct)) {
          throw new Error(
            `cabinet_not_confirmed: final Ads Manager URL does not confirm act=${resolvedAct}`,
          );
        }
      } catch (error) {
        this.failRolePageNavigation(session, {
          page,
          resolvedAct,
          ownPages,
          cabinetKey,
          error,
          signal: opts.signal,
        });
      }
    }

    throwIfOperationAborted(opts.signal);
    if (!isConfirmedAdsManagerPage(page, resolvedAct)) {
      throw new Error(
        `cabinet_not_confirmed: selected page does not confirm act=${resolvedAct}`,
      );
    }
    if (opposite.has(page)) {
      // Fail closed: never silently degrade to a shared page.
      throw new Error(
        `Нарушение изоляции: ${role} page уже принадлежит другой роли`,
      );
    }
    // Кабинет открылся — значит сессия Facebook жива: паузу снимаем сразу, не
    // дожидаясь конца интервала.
    this.clearLoginRequired(session);
    this.openFailures.delete(this.openFailureKey(session, resolvedAct));
    ownPages.set(cabinetKey, page);
    if (role !== "scan") {
      void this.recordPageTargetId(page, role, resolvedAct);
    }
    session.status = "connected";
    rememberAdsManagerUrl(session, page);
    if (role === "scan") session.primaryPage = page;
    return page;
  }

  listSessions(): Array<{ id: string; status: string; connectedAt: string }> {
    const result: Array<{ id: string; status: string; connectedAt: string }> =
      [];
    for (const [id, session] of this.sessions) {
      result.push({
        id,
        status: session.status,
        connectedAt: session.connectedAt.toISOString(),
      });
    }
    return result;
  }

  /**
   * Запоминает targetId вкладки вместе с её ролью. Fire-and-forget; сбой
   * игнорируется — страница без targetId просто не восстанавливается при реконнекте.
   */
  private async recordPageTargetId(
    page: Page,
    role: BrowserPageRole,
    actId: string,
  ): Promise<void> {
    try {
      const cdpSession = await page.context().newCDPSession(page);
      let targetId: string | undefined;
      try {
        const info = (await cdpSession.send("Target.getTargetInfo")) as {
          targetInfo?: { targetId?: string };
        };
        targetId = info?.targetInfo?.targetId;
      } finally {
        await cdpSession.detach().catch(() => undefined);
      }
      if (targetId) {
        this.pageRolesByTargetId.set(targetId, { role, actId });
      }
    } catch {
      // best-effort: страница без targetId не восстанавливается после реконнекта
    }
  }

  /**
   * После переподключения CDP перебирает все вкладки нового браузера, сверяет
   * их targetId с картой ролей и восстанавливает реестры. Вкладки, чей targetId
   * не удалось получить или не найден в карте, не попадают ни в один реестр —
   * деградация в пустой реестр, а не в усыновление.
   */
  private async restoreRolePagesAfterReconnect(browser: Browser): Promise<{
    scanPages: Map<string, Page>;
    controlPages: Map<string, Page>;
    interactivePages: Map<string, Page>;
  }> {
    const scanPages = new Map<string, Page>();
    const controlPages = new Map<string, Page>();
    const interactivePages = new Map<string, Page>();

    if (this.pageRolesByTargetId.size === 0) {
      return { scanPages, controlPages, interactivePages };
    }

    for (const context of safeBrowserContexts(browser)) {
      for (const page of safeContextPages(context)) {
        if (isPageClosed(page)) continue;
        try {
          const cdpSession = await page.context().newCDPSession(page);
          let targetId: string | undefined;
          try {
            const info = (await cdpSession.send("Target.getTargetInfo")) as {
              targetInfo?: { targetId?: string };
            };
            targetId = info?.targetInfo?.targetId;
          } finally {
            await cdpSession.detach().catch(() => undefined);
          }
          if (!targetId) continue;
          const roleInfo = this.pageRolesByTargetId.get(targetId);
          if (!roleInfo) continue;
          const { role, actId } = roleInfo;
          if (role === "control") {
            controlPages.set(actId, page);
            _agentMoneyPages.add(page);
            _agentControlPages.add(page);
          } else if (role === "interactive") {
            interactivePages.set(actId, page);
            _agentMoneyPages.add(page);
          }
          // scan не восстанавливается: он переоткрывается или усыновляется штатно
        } catch {
          // targetId недоступен — страница не восстанавливается
        }
      }
    }

    return { scanPages, controlPages, interactivePages };
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
      throw new Error(
        `CDP endpoint профиля ${profileId} на порту ${port} не стал доступен`,
      );
    }
    throwIfOperationAborted(signal);
    const cdpUrl = `http://127.0.0.1:${port}`;
    const connection = chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
    if (!signal) {
      return connection;
    }
    return new Promise<Browser>((resolve, reject) => {
      const onAbort = () => {
        signal.removeEventListener("abort", onAbort);
        reject(new Error("Browser lifecycle operation cancelled"));
      };
      if (signal.aborted) {
        onAbort();
      } else {
        signal.addEventListener("abort", onAbort, { once: true });
      }
      void connection.then(
        (browser) => {
          signal.removeEventListener("abort", onAbort);
          if (signal.aborted) {
            browser.removeAllListeners();
            return;
          }
          resolve(browser);
        },
        (error) => {
          signal.removeEventListener("abort", onAbort);
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
    // Остановка наша — значит последующее исчезновение из /list ожидаемо и
    // запускать профиль заново законно.
    this.selfStoppedProfiles.add(profileId);
    try {
      return await visionClient.restartProfileToRecoverPort(
        folderId,
        profileId,
        {
          stopTimeoutSec: RECOVERY_STOP_TIMEOUT_SECONDS,
          portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
          settleAfterStopMs: RECOVERY_SETTLE_DELAY_MS,
          signal,
        },
      );
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
    throw new Error("Browser lifecycle operation cancelled");
  }
}

/**
 * Контекст навигации для следа: без него запись «страницу увели» не отвечает
 * на единственный вопрос, ради которого её читают, — чью и кто.
 */
interface NavigationTraceContext {
  session: string;
  role: string;
  act: string;
  by: string;
}

async function navigatePageWithinOperation(
  page: Page,
  targetUrl: string,
  ctx: NavigationTraceContext,
  signal?: AbortSignal,
): Promise<void> {
  throwIfOperationAborted(signal);
  // Запись уходит ДО навигации: зависшая навигация тоже должна быть видна, а
  // разбор 19.08 упёрся именно в вопрос «был ли reload в эти десять секунд».
  tracePageNav({ ...ctx, kind: "goto", url: targetUrl });
  await raceWithAbort(
    page.goto(targetUrl, { waitUntil: "domcontentloaded" }),
    signal,
  );
  throwIfOperationAborted(signal);
}

async function reloadPageWithinOperation(
  page: Page,
  ctx: NavigationTraceContext,
  signal?: AbortSignal,
): Promise<void> {
  throwIfOperationAborted(signal);
  tracePageNav({ ...ctx, kind: "reload", url: safePageUrl(page) });
  const onAbort = (): void => {
    // Playwright cannot cancel page.reload directly. Closing the isolated role
    // page terminates the navigation and guarantees no browser work survives
    // the fenced gRPC request.
    void page.close({ runBeforeUnload: false }).catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    await raceWithAbort(
      page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 }),
      signal,
    );
    throwIfOperationAborted(signal);
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

export const PROFILE_TAKEN_ELSEWHERE_MARKER = "cabinet_profile_taken_elsewhere";

function buildProfileTakenElsewhereError(profileId: string): Error {
  return new Error(
    `${PROFILE_TAKEN_ELSEWHERE_MARKER}: профиль ${profileId} забрало другое ` +
      "подключение. Перезапуск развернул бы облачный снимок поверх живой сессии " +
      "и разлогинил бы кабинет, поэтому запуск не выполняется. " +
      "Один профиль Vision — один потребитель.",
  );
}

function buildMaintenanceRecoveryRequiredError(profileId: string): Error {
  return new Error(
    `Профиль ${profileId} не имеет готового CDP-порта. ` +
      "Требуется capability-authorized maintenance recovery.",
  );
}
