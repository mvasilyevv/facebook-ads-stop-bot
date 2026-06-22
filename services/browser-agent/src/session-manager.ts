import { chromium } from 'playwright';
import type { Browser, Page } from 'playwright';
import { v4 as uuidv4 } from 'uuid';
import { VisionClient } from './vision-client.js';
import { STEALTH_INIT_SCRIPT } from './stealth.js';
import { generateHumanProfile } from './humanizer.js';
import { injectCreator } from './creator-injector.js';
import { adsManagerColumnsQs } from './am/am-columns-preset.js';
import { withPageLock } from './page-lock.js';
import type { BrowserSession, HumanProfile } from './types.js';

const EXISTING_PROFILE_PORT_GRACE_SECONDS = 8;
const START_PROFILE_PORT_WAIT_SECONDS = 20;
const CDP_READY_WAIT_SECONDS = 20;
const RECOVERY_STOP_TIMEOUT_SECONDS = 20;
const RECOVERY_SETTLE_DELAY_MS = 1_000;
const ADS_MANAGER_URL_MARKERS = ['adsmanager', 'facebook.com/ads'];
const DISABLED_FLAG_VALUES = new Set(['0', 'false', 'no', 'off']);

function isAutoRestartOnMissingCdpEnabled(): boolean {
  const rawValue = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
  if (rawValue == null || String(rawValue).trim() === '') {
    return true;
  }
  const normalized = String(rawValue).trim().toLowerCase();
  if (DISABLED_FLAG_VALUES.has(normalized)) {
    return false;
  }
  return true;
}

export function isAdsManagerUrl(url: string | null | undefined): boolean {
  const normalized = String(url || '').toLowerCase();
  return ADS_MANAGER_URL_MARKERS.some((marker) => normalized.includes(marker));
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
export function findAdsManagerPageByAct(browser: Browser | null, actId: string): Page | null {
  if (!browser) {
    return null;
  }
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (typeof page.isClosed === 'function' && page.isClosed()) {
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
export function findReusableNonCabinetPage(browser: Browser | null): Page | null {
  if (!browser) {
    return null;
  }
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (isPageClosed(page)) {
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

  async startBrowser(options: {
    visionXToken: string;
    visionApiUrl: string;
    visionProfileId: string;
    visionFolderId?: string;
    viewportWidth?: number;
    viewportHeight?: number;
  }): Promise<BrowserSession> {
    const {
      visionXToken,
      visionApiUrl,
      visionProfileId,
      visionFolderId,
    } = options;

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Vision API иногда требует folder_id отдельно, поэтому восстанавливаем его по profile_id.
    let folderId = visionFolderId;
    if (!folderId) {
      folderId = await visionClient.resolveFolderId(visionProfileId);
    }

    console.log(`[session-manager] startBrowser: profile=${visionProfileId} folder=${folderId}`);
    const existingProfile = await visionClient.getProfile(visionProfileId);
    console.log(
      `[session-manager] /list для ${visionProfileId}: ${
        existingProfile ? `port=${existingProfile.port}` : 'НЕТ в списке'
      }`,
    );
    let profile: { port: number | null };

    if (existingProfile?.port) {
      // Не стартуем второй экземпляр профиля, иначе можно потерять открытую вкладку.
      console.log(`[session-manager] профиль уже с CDP-портом ${existingProfile.port}, использую как есть`);
      profile = { port: existingProfile.port };
    } else if (existingProfile) {
      // У Vision порт иногда появляется с задержкой, поэтому сначала даем ему короткий grace period.
      console.log(`[session-manager] профиль без CDP, жду до ${EXISTING_PROFILE_PORT_GRACE_SECONDS}с`);
      const delayedPort = await visionClient.waitUntilProfileHasPort(
        visionProfileId,
        EXISTING_PROFILE_PORT_GRACE_SECONDS,
      );
      if (delayedPort) {
        console.log(`[session-manager] порт появился сам: ${delayedPort}`);
        profile = { port: delayedPort };
      } else if (isAutoRestartOnMissingCdpEnabled()) {
        // Перезапуск уже открытого профиля потенциально разрушителен, поэтому он только по feature flag.
        console.log(`[session-manager] auto-restart включён, перезапускаю профиль stop+start`);
        profile = await this.restartProfileForMissingCdp(
          visionClient,
          folderId,
          visionProfileId,
        );
        console.log(`[session-manager] restartProfileForMissingCdp вернул port=${profile.port}`);
      } else {
        throw buildMissingCdpRestartDisabledError(visionProfileId);
      }
    } else {
      try {
        // Если Vision не поднял CDP-порт, рестарт разрешён только явным feature flag.
        console.log(`[session-manager] профиль не запущен, стартую через /start`);
        profile = await visionClient.startProfile(folderId, visionProfileId, {
          portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
        });
        console.log(`[session-manager] /start вернул port=${profile.port}`);
      } catch (error) {
        console.log(`[session-manager] /start упал: ${error instanceof Error ? error.message : String(error)}`);
        if (!isMissingCdpPortError(error)) {
          throw error;
        }
        if (!isAutoRestartOnMissingCdpEnabled()) {
          throw buildMissingCdpRestartDisabledError(visionProfileId);
        }
        profile = await this.restartProfileForMissingCdp(
          visionClient,
          folderId,
          visionProfileId,
        );
      }
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
      await injectCreator(contexts[0]);
    }
    let primaryPage = findPreferredPrimaryPage(browser);
    if (!primaryPage && contexts[0]) {
      primaryPage = await contexts[0].newPage();
    }
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
      humanProfile,
      connectedAt: new Date(),
      status: 'connected',
    };

    this.sessions.set(session.id, session);
    return session;
  }

  async disconnectBrowser(sessionId: string): Promise<void> {
    const session = this.getSession(sessionId);
    // Для CDP-подключения browser.close() закрывает сам удалённый Vision-профиль.
    // Здесь нужен только логический разрыв сессии на стороне browser-agent.
    session.browser = null;
    session.primaryPage = null;
    session.playwright = null;
    session.status = 'disconnected';
  }

  async stopBrowser(sessionId: string): Promise<void> {
    const session = this.getSession(sessionId);

    // Здесь stopBrowser уже осознанно завершает удаленный профиль Vision.
    if (session.browser) {
      try {
        await session.browser.close();
      } catch {
        // Ошибка закрытия не должна мешать остановке профиля через Vision API.
      }
    }

    // Завершаем профиль через штатный API Vision после закрытия CDP-клиента.
    try {
      const visionClient = new VisionClient(session.visionXToken, session.visionApiUrl);
      await visionClient.stopProfile(session.visionFolderId, session.visionProfileId);
    } catch {
      // Повторная остановка может упасть, если профиль уже закрыт пользователем.
    }

    this.sessions.delete(sessionId);
  }

  async reconnectBrowser(sessionId: string, options?: {
    visionXToken?: string;
    visionApiUrl?: string;
    visionProfileId?: string;
    // Принудительный рестарт Vision-профиля даже при живом CDP-порте — для авто-исцеления
    // «сеть страницы мертва» (порт на месте, но fetch не уходит): reconnect к тому же порту
    // сеть не оживляет, помогает только перезапуск профиля. Обходит env-gate авто-restart.
    forceProfileRestart?: boolean;
  }): Promise<BrowserSession> {
    const session = this.getSession(sessionId);
    // Старый CDP-клиент — отвяжем его ПОСЛЕ успешного нового подключения (H-6/BA-2),
    // чтобы не копить ws-соединения и listeners под recovery-нагрузкой.
    const oldBrowser = session.browser;

    const visionXToken = options?.visionXToken ?? session.visionXToken;
    const visionApiUrl = options?.visionApiUrl ?? session.visionApiUrl;
    const visionProfileId = options?.visionProfileId ?? session.visionProfileId;

    const visionClient = new VisionClient(visionXToken, visionApiUrl);

    // Переподключение сначала пытается забрать уже существующий CDP-порт и не трогать окно профиля.
    const existingProfile = await visionClient.getProfile(visionProfileId);
    const resolvedFolderId = session.visionProfileId === visionProfileId
      ? session.visionFolderId
      : await visionClient.resolveFolderId(visionProfileId);

    const forceRestart = options?.forceProfileRestart === true;
    let resolvedPort = forceRestart ? null : (existingProfile?.port ?? null);
    if (!resolvedPort && existingProfile && !forceRestart) {
      resolvedPort = await visionClient.waitUntilProfileHasPort(
        visionProfileId,
        EXISTING_PROFILE_PORT_GRACE_SECONDS,
      );
    }

    // forceRestart обходит env-gate (явное лечение, а не авто-restart на missing CDP).
    if (!resolvedPort && existingProfile && (forceRestart || isAutoRestartOnMissingCdpEnabled())) {
      const recoveredProfile = await this.restartProfileForMissingCdp(
        visionClient,
        resolvedFolderId,
        visionProfileId,
      );
      resolvedPort = recoveredProfile.port;
    }

    if (!resolvedPort) {
      if (existingProfile) {
        throw buildMissingCdpRestartDisabledError(visionProfileId);
      }
      throw new Error(`Профиль ${visionProfileId} не запущен или не имеет CDP-порта`);
    }

    const browser = await this.connectOverReadyCdp(
      visionClient,
      visionProfileId,
      resolvedPort,
    );

    // Повторно добавляем stealth в существующий контекст после нового CDP-подключения.
    const contexts = browser.contexts();
    if (contexts.length > 0) {
      await contexts[0].addInitScript(STEALTH_INIT_SCRIPT);
      await injectCreator(contexts[0]);
    }

    // Сохраняем текущую вкладку как primaryPage, чтобы восстановить работу без навигации.
    let primaryPage = findPreferredPrimaryPage(browser);
    if (!primaryPage && contexts[0]) {
      primaryPage = await contexts[0].newPage();
    }

    session.browser = browser;
    session.primaryPage = primaryPage;
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

  // Авто-исцеление при «живая страница/CDP, но мёртвая сеть» (Failed to fetch / code -2).
  // Эскалация по session.healLevel: 0 → reload страницы, 1 → CDP-reconnect, 2+ → рестарт
  // Vision-профиля (реально оживляет сеть). Всё под per-session page-lock, чтобы лечение не
  // пересекалось с in-flight scan/mutation. Детект/cooldown — в session-health.ts (вызывающий
  // решает, звать ли healSessionNetwork). Никогда не бросает наружу — best-effort.
  async healSessionNetwork(sessionId: string): Promise<{ action: string; ok: boolean }> {
    const session = this.getSession(sessionId);
    const level = session.healLevel ?? 0;
    let ok = true;
    const action = await withPageLock(sessionId, async (): Promise<string> => {
      try {
        if (level <= 0) {
          const page = session.primaryPage;
          const closed = typeof page?.isClosed === 'function' && page.isClosed();
          if (page && !closed) {
            await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 });
          }
          return 'reload';
        }
        if (level === 1) {
          await this.reconnectBrowser(sessionId);
          return 'reconnect';
        }
        await this.reconnectBrowser(sessionId, { forceProfileRestart: true });
        return 'restart_profile';
      } catch (err) {
        ok = false;
        console.error(`[heal] session=${sessionId} уровень=${level} ошибка лечения:`, err);
        return level <= 0 ? 'reload' : level === 1 ? 'reconnect' : 'restart_profile';
      }
    });
    session.healLevel = level + 1;
    session.lastHealAt = new Date();
    session.netFailureStreak = 0;
    console.warn(
      `[heal] session=${sessionId} action=${action} ok=${ok} → следующий уровень=${session.healLevel}`,
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

  /**
   * Гарантирует живую primary-вкладку Ads Manager для скан-цикла (self-heal Layer 1).
   *
   * Сценарии:
   *  - Живая вкладка Ads Manager НАШЕГО кабинета открыта → используем её (и запоминаем URL).
   *  - Открыта вкладка ДРУГОГО кабинета (act не совпал с ожидаемым) → не сканируем чужой act,
   *    переоткрываем свой кабинет ниже. Защита от тихой слепоты MV при нескольких кабинетах.
   *  - Вкладку закрыли, но CDP/браузер живы → переоткрываем НОВУЮ вкладку на последнем
   *    known-good URL кабинета (или реконструированном из act_id). Чужие вкладки не трогаем.
   *  - Браузер/CDP мертвы или URL кабинета неизвестен → бросаем
   *    'Основная страница браузера недоступна' (эскалация на observer: reconnect/StartBrowser).
   *
   * В общем кабинете НЕ угадываем дефолтный act — иначе можно открыть чужой кабинет.
   */
  async ensureAdsManagerPage(
    session: BrowserSession,
    opts: { fallbackUrl?: string; actId?: string } = {},
  ): Promise<Page> {
    // --- Мульти-кабинет: явный actId — детерминированный путь без угадываний. ---
    // Ищем вкладку нужного кабинета среди ВСЕХ открытых; нет — открываем новую.
    // session.primaryPage/lastAdsManagerUrl НЕ трогаем: одно-кабинетный legacy-путь
    // и мутации без ad_account_id продолжают работать как раньше.
    if (opts.actId) {
      const existing = findAdsManagerPageByAct(session.browser, opts.actId);
      if (existing) {
        // НЕ активируем вкладку: скан идёт через am_tabular (page.evaluate(fetch) по
        // graph-каналу), DOM не трогается, фокус вкладки не нужен. bringToFront() здесь
        // только воровал фокус у пользователя на каждом цикле (Vision выскакивал на экран).
        return existing;
      }
      const browserForAct = session.browser;
      const alive =
        browserForAct &&
        (typeof browserForAct.isConnected !== 'function' || browserForAct.isConnected());
      const ctxForAct = alive ? browserForAct.contexts()[0] : undefined;
      if (!ctxForAct) {
        throw new Error('Основная страница браузера недоступна');
      }
      const url = adsManagerUrlForAct(opts.actId);
      // Переиспользуем уже открытую нейтральную вкладку (исходная FB / about:blank), чтобы не
      // плодить вкладки: первый кабинет занимает её, остальные открываются новыми. Кабинетные
      // вкладки (?act=) при этом не трогаем (findReusableNonCabinetPage их исключает) —
      // защита от навигации чужого кабинета.
      const reusable = findReusableNonCabinetPage(browserForAct);
      if (reusable) {
        console.warn(
          `[session-manager] act=${opts.actId}: переиспользую вкладку ${reusable.url()} → ${url}`,
        );
        await reusable.goto(url, { waitUntil: 'domcontentloaded' });
        return reusable;
      }
      console.warn(`[session-manager] вкладка кабинета act=${opts.actId} не найдена — открываю ${url}`);
      const newPage = await ctxForAct.newPage();
      await newPage.goto(url, { waitUntil: 'domcontentloaded' });
      return newPage;
    }

    // Ожидаемый кабинет: последний known-good URL → реконструкция из act_id (передаёт caller).
    const targetUrl = session.lastAdsManagerUrl || opts.fallbackUrl;
    const expectedAct = extractAdAccountId(targetUrl);

    // 1. Живая вкладка Ads Manager уже открыта? Решаем по совпадению act с ожидаемым кабинетом.
    const preferred = findPreferredPrimaryPage(session.browser);
    if (preferred && !isPageClosed(preferred) && isAdsManagerUrl(preferred.url())) {
      const preferredAct = extractAdAccountId(preferred.url());
      if (expectedAct && preferredAct !== null && preferredAct === expectedAct) {
        // Ожидаемый кабинет известен и совпал — строгий путь.
        session.primaryPage = preferred;
        rememberAdsManagerUrl(session, preferred);
        return preferred;
      }
      if (!expectedAct) {
        // Самый первый цикл свежего browser-agent: act ещё не сниффился, эталона для сверки нет
        // (trust-on-first-use). Принимаем открытую вкладку, но НЕ молча — логируем, чтобы случай
        // был виден. Деньги защищены owner-scoping'ом (am_tabular фильтрует по owner_tag); со
        // следующего цикла act известен из GraphContext → строгая сверка выше.
        console.warn(
          '[session-manager] первый цикл: ожидаемый act неизвестен, принимаю открытую вкладку '
            + `${preferred.url()} (trust-on-first-use; далее — строгая сверка act)`,
        );
        session.primaryPage = preferred;
        rememberAdsManagerUrl(session, preferred);
        return preferred;
      }
      // expectedAct известен, но не совпал — другой кабинет; логируем и переоткрываем свой ниже.
      console.warn(
        `[session-manager] открытая вкладка — другой кабинет (act=${preferredAct} != ${expectedAct}), `
          + 'переоткрываю свой',
      );
    }

    // 2. Браузер/CDP живы? Если нет — восстановление не на этом уровне (нужен reconnect).
    const browser = session.browser;
    if (!browser || (typeof browser.isConnected === 'function' && !browser.isConnected())) {
      throw new Error('Основная страница браузера недоступна');
    }

    // 3. Открываем свой кабинет по known-good/реконструированному URL.
    const context = browser.contexts()[0];
    if (!targetUrl || !context) {
      throw new Error('Основная страница браузера недоступна');
    }

    // 4. Открываем НОВУЮ вкладку (чужие не трогаем) и переходим на кабинет.
    console.warn(
      `[session-manager] primary-вкладка Ads Manager недоступна — переоткрываю на ${targetUrl}`,
    );
    const page = await context.newPage();
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
    session.primaryPage = page;
    session.status = 'connected';
    rememberAdsManagerUrl(session, page);
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
  ): Promise<Browser> {
    const ready = await visionClient.waitUntilCdpReady(port, CDP_READY_WAIT_SECONDS);
    if (!ready) {
      throw new Error(`CDP endpoint профиля ${profileId} на порту ${port} не стал доступен`);
    }
    const cdpUrl = `http://127.0.0.1:${port}`;
    return chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
  }

  private async restartProfileForMissingCdp(
    visionClient: VisionClient,
    folderId: string,
    profileId: string,
  ): Promise<{ port: number | null }> {
    try {
      return await visionClient.restartProfileToRecoverPort(folderId, profileId, {
        stopTimeoutSec: RECOVERY_STOP_TIMEOUT_SECONDS,
        portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
        settleAfterStopMs: RECOVERY_SETTLE_DELAY_MS,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Не удалось восстановить CDP-порт автоперезапуском профиля ${profileId}: ${message}`,
      );
    }
  }
}

function buildMissingCdpRestartDisabledError(profileId: string): Error {
  return new Error(
    `Профиль ${profileId} запущен без CDP-порта. `
    + 'Автоперезапуск профиля для восстановления CDP-порта отключён. '
    + 'Уберите VISION_AUTO_RESTART_ON_MISSING_CDP=false или перезапустите профиль вручную.',
  );
}

function isMissingCdpPortError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return error.message.toLowerCase().includes('cdp-порт');
}
