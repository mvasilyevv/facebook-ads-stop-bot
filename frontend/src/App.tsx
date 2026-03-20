import { FormEvent, useEffect, useState, startTransition } from "react";
import { Badge } from "./components/Badge";
import { EmptyState } from "./components/EmptyState";
import { SectionCard } from "./components/SectionCard";
import {
  blockAd,
  createOffer,
  createOfferBinding,
  createOfferRate,
  loadDashboard,
  saveRule,
  startSession,
  stopSession,
  unblockAd,
} from "./lib/api";
import {
  formatDateTime,
  formatMoney,
  formatRelativeStatus,
} from "./lib/format";
import type {
  AdSummary,
  BrowserSessionItem,
  DecisionItem,
  HealthResponse,
  OfferItem,
  RuleItem,
} from "./types";

type DashboardState = {
  loading: boolean;
  refreshing: boolean;
  errors: Record<string, string>;
  health: HealthResponse | null;
  ads: AdSummary[];
  decisions: DecisionItem[];
  rules: RuleItem[];
  offers: OfferItem[];
  sessions: BrowserSessionItem[];
  lastLoadedAt: string | null;
};

const initialState: DashboardState = {
  loading: true,
  refreshing: false,
  errors: {},
  health: null,
  ads: [],
  decisions: [],
  rules: [],
  offers: [],
  sessions: [],
  lastLoadedAt: null,
};

const emptyOfferForm = {
  code: "",
  name: "",
  isActive: true,
};

const emptyRateForm = {
  offerId: "",
  cpaUsd: "",
  effectiveFrom: "",
  note: "",
};

const emptyBindingForm = {
  targetType: "adset" as "adset" | "ad",
  entityId: "",
  offerId: "",
  priority: "0",
  isActive: true,
};

const emptySessionReason = "Запрос оператора";
const emptyAdBlockReason = "Ручная блокировка через UI";

function getBadgeTone(value: string): "neutral" | "good" | "warn" | "bad" | "info" {
  if (value.includes("ok") || value.includes("active") || value.includes("succes")) {
    return "good";
  }
  if (
    value.includes("warn") ||
    value.includes("learning") ||
    value.includes("paused") ||
    value.includes("stopped")
  ) {
    return "warn";
  }
  if (value.includes("error") || value.includes("fail") || value.includes("reject")) {
    return "bad";
  }
  if (value.includes("manual") || value.includes("block") || value.includes("disabled")) {
    return "info";
  }
  return "neutral";
}

export default function App() {
  const [state, setState] = useState<DashboardState>(initialState);
  const [adSearch, setAdSearch] = useState("");
  const [ruleSearch, setRuleSearch] = useState("");
  const [decisionSearch, setDecisionSearch] = useState("");
  const [offerSearch, setOfferSearch] = useState("");
  const [sessionSearch, setSessionSearch] = useState("");
  const [adBlockReason, setAdBlockReason] = useState(emptyAdBlockReason);
  const [sessionReason, setSessionReason] = useState(emptySessionReason);
  const [offerForm, setOfferForm] = useState(emptyOfferForm);
  const [rateForm, setRateForm] = useState(emptyRateForm);
  const [bindingForm, setBindingForm] = useState(emptyBindingForm);
  const [message, setMessage] = useState<string | null>(null);

  async function reloadDashboard(isSilent = false) {
    if (isSilent) {
      setState((current) => ({ ...current, refreshing: true }));
    } else {
      setState((current) => ({ ...current, loading: true, errors: {} }));
    }

    try {
      const data = await loadDashboard();
      startTransition(() => {
        setState({
          loading: false,
          refreshing: false,
          errors: data.errors,
          health: data.health,
          ads: data.ads,
          decisions: data.decisions,
          rules: data.rules,
          offers: data.offers,
          sessions: data.sessions,
          lastLoadedAt: new Date().toISOString(),
        });
      });
      setMessage("Данные успешно обновлены");
    } catch (error) {
      const fallback = error instanceof Error ? error.message : "Не удалось загрузить данные";
      setMessage(fallback);
      setState((current) => ({
        ...current,
        loading: false,
        refreshing: false,
        errors: { dashboard: fallback },
      }));
    }
  }

  useEffect(() => {
    void reloadDashboard(false);
  }, []);

  async function runAction(
    action: () => Promise<unknown>,
    successMessage: string,
    sectionKey?: string,
  ) {
    setMessage(null);
    try {
      await action();
      setMessage(successMessage);
      await reloadDashboard(true);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Операция не выполнена";
      setMessage(text);
      if (sectionKey) {
        setState((current) => ({
          ...current,
          errors: { ...current.errors, [sectionKey]: text },
        }));
      }
    }
  }

  const visibleAds = state.ads.filter((ad) => {
    const text = `${ad.fb_ad_id} ${ad.ad_name} ${ad.adset_name} ${ad.campaign_name}`.toLowerCase();
    const matchesSearch = text.includes(adSearch.toLowerCase());
    return matchesSearch;
  });

  const visibleRules = state.rules.filter((rule) => {
    return `${rule.code} ${rule.title} ${rule.description ?? ""}`.toLowerCase().includes(ruleSearch.toLowerCase());
  });

  const visibleDecisions = state.decisions.filter((decision) => {
    return `${decision.fb_ad_id} ${decision.rule_id ?? ""} ${decision.reason} ${decision.decision}`.toLowerCase().includes(
      decisionSearch.toLowerCase(),
    );
  });

  const visibleOffers = state.offers.filter((offer) => {
    return `${offer.code} ${offer.name}`.toLowerCase().includes(offerSearch.toLowerCase());
  });

  const visibleSessions = state.sessions.filter((session) => {
    return `${session.profile_id} ${session.browser_host_id} ${session.status}`.toLowerCase().includes(
      sessionSearch.toLowerCase(),
    );
  });

  const systemTone = state.health ? getBadgeTone(state.health.status) : "warn";
  const systemTitle = state.health ? `Система ${formatRelativeStatus(state.health.status)}` : "Система не загружена";

  async function onSubmitOffer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(
      () =>
        createOffer({
          code: offerForm.code,
          name: offerForm.name,
          is_active: offerForm.isActive,
        }),
      "Оффер создан",
      "offers",
    );
    setOfferForm(emptyOfferForm);
  }

  async function onSubmitRate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!rateForm.offerId) {
      setMessage("Выберите оффер для ставки");
      return;
    }
    await runAction(
      () =>
        createOfferRate(rateForm.offerId, {
          cpa_usd: rateForm.cpaUsd,
          effective_from: rateForm.effectiveFrom,
          note: rateForm.note || undefined,
        }),
      "Ставка оффера сохранена",
      "offers",
    );
    setRateForm(emptyRateForm);
  }

  async function onSubmitBinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!bindingForm.offerId || !bindingForm.entityId) {
      setMessage("Заполните оффер и ID сущности");
      return;
    }
    await runAction(
      () =>
        createOfferBinding({
          path: bindingForm.targetType,
          entityId: bindingForm.entityId,
          offerId: bindingForm.offerId,
          priority: Number(bindingForm.priority) || 0,
          isActive: bindingForm.isActive,
        }),
      "Оффер привязан",
      "offers",
    );
    setBindingForm(emptyBindingForm);
  }

  return (
    <div className="app-shell">
      <div className="background-grid" />
      <header className="hero">
        <div className="hero__copy">
          <p className="eyebrow">Панель управления Facebook Ads</p>
          <h1>Наблюдаем, разбираем решения и держим стек под контролем</h1>
          <p className="hero__lead">
            Один экран для здоровья системы, объявлений, решений, правил, офферов и browser host сессий.
            Все тексты в интерфейсе на русском, ошибки тоже объясняются по-человечески.
          </p>
        </div>
        <div className="hero__aside">
          <div className={`hero-card hero-card--${systemTone}`}>
            <span className="hero-card__label">Состояние backend</span>
            <strong>{systemTitle}</strong>
            <span>{state.health ? `Окружение: ${state.health.environment}` : "Ожидание ответа от API"}</span>
          </div>
          <div className="hero-card">
            <span className="hero-card__label">Последнее обновление</span>
            <strong>{state.lastLoadedAt ? formatDateTime(state.lastLoadedAt) : "Пока не загружено"}</strong>
            <span>{state.refreshing ? "Идёт обновление данных" : "Данные обновляются по кнопке"}</span>
          </div>
          <div className="hero-actions">
            <button type="button" className="button button--primary" onClick={() => void reloadDashboard(true)}>
              {state.refreshing ? "Обновляем..." : "Обновить данные"}
            </button>
            <a className="button button--ghost" href="#ads">
              Перейти к объявлениям
            </a>
          </div>
        </div>
      </header>

      {message ? <div className="message-banner">{message}</div> : null}

      <nav className="section-nav">
        <a href="#health">Здоровье системы</a>
        <a href="#ads">Объявления</a>
        <a href="#decisions">Решения</a>
        <a href="#rules">Правила</a>
        <a href="#offers">Офферы</a>
        <a href="#sessions">Browser host</a>
      </nav>

      <main className="dashboard">
        <SectionCard
          title="Здоровье системы"
          subtitle="Краткая сводка по backend и очередности загруженных данных"
          actions={
            <span className="section-note">
              {state.health?.timestamp ? `Снимок: ${formatDateTime(state.health.timestamp)}` : "Снимок отсутствует"}
            </span>
          }
        >
          <div className="metric-grid" id="health">
            <article className="metric-tile metric-tile--accent">
              <span>Сервис</span>
              <strong>{state.health?.service ?? "API"}</strong>
            </article>
            <article className="metric-tile">
              <span>Статус</span>
              <strong>{state.health ? formatRelativeStatus(state.health.status) : "нет ответа"}</strong>
            </article>
            <article className="metric-tile">
              <span>Окружение</span>
              <strong>{state.health?.environment ?? "неизвестно"}</strong>
            </article>
            <article className="metric-tile">
              <span>База данных</span>
              <strong>{state.health?.database_status ?? "нет данных"}</strong>
            </article>
          </div>
          {state.errors.health ? <div className="inline-error">{state.errors.health}</div> : null}
        </SectionCard>

        <SectionCard
          title="Объявления"
          subtitle="Текущее состояние ads, статус доставки, tracking mode и ручные действия"
          actions={
            <div className="section-actions">
              <input
                className="input input--compact"
                value={adSearch}
                onChange={(event) => setAdSearch(event.target.value)}
                placeholder="Поиск по ad ID, названию или кампании"
              />
              <input
                className="input input--compact"
                value={adBlockReason}
                onChange={(event) => setAdBlockReason(event.target.value)}
                placeholder="Причина блокировки"
              />
            </div>
          }
        >
          {state.errors.ads ? <div className="inline-error">{state.errors.ads}</div> : null}
          <div className="table-wrap" id="ads">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Объявление</th>
                  <th>Статус</th>
                  <th>Режим</th>
                  <th>CPA</th>
                  <th>Последнее решение</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {visibleAds.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState title="Объявлений нет" description="После загрузки backend здесь появится список ads." />
                    </td>
                  </tr>
                ) : (
                  visibleAds.map((ad) => (
                    <tr key={ad.fb_ad_id}>
                      <td>
                        <strong>{ad.ad_name}</strong>
                        <div className="muted">
                          {ad.campaign_name} · {ad.adset_name}
                        </div>
                        <div className="mono">{ad.fb_ad_id}</div>
                      </td>
                      <td>
                        <Badge tone={getBadgeTone(ad.delivery_status)}>{formatRelativeStatus(ad.delivery_status)}</Badge>
                      </td>
                      <td>
                        <Badge tone={getBadgeTone(ad.tracking_mode)}>{formatRelativeStatus(ad.tracking_mode)}</Badge>
                      </td>
                      <td>{formatMoney(ad.resolved_cpa_usd)}</td>
                      <td>{formatRelativeStatus(ad.last_decision)}</td>
                      <td>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="button button--small"
                            onClick={() =>
                              void runAction(
                                () => blockAd(ad.fb_ad_id, adBlockReason),
                                `Объявление ${ad.fb_ad_id} заблокировано`,
                                "ads",
                              )
                            }
                          >
                            Заблокировать
                          </button>
                          <button
                            type="button"
                            className="button button--small button--ghost"
                            onClick={() =>
                              void runAction(
                                () => unblockAd(ad.fb_ad_id),
                                `Объявление ${ad.fb_ad_id} возвращено в отслеживание`,
                                "ads",
                              )
                            }
                          >
                            Разблокировать
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>

        <SectionCard
          title="Решения"
          subtitle="История would_pause / would_resume и итоговых действий"
          actions={
            <input
              className="input input--compact"
              value={decisionSearch}
              onChange={(event) => setDecisionSearch(event.target.value)}
              placeholder="Поиск по ad ID, правилу или причине"
            />
          }
        >
          {state.errors.decisions ? <div className="inline-error">{state.errors.decisions}</div> : null}
          <div className="timeline" id="decisions">
            {visibleDecisions.length === 0 ? (
              <EmptyState
                title="Решений пока нет"
                description="Когда backend начнёт писать decisions, они появятся в этой ленте."
              />
            ) : (
              visibleDecisions.map((decision) => (
                <article key={decision.id} className="timeline-item">
                  <div className="timeline-item__head">
                    <div>
                      <strong>{decision.fb_ad_id}</strong>
                      <div className="muted">
                        Скан {decision.scan_run_id}
                        {decision.rule_id ? ` · Правило ${decision.rule_id}` : ""}
                      </div>
                    </div>
                    <Badge tone={getBadgeTone(decision.decision)}>{formatRelativeStatus(decision.decision)}</Badge>
                  </div>
                  <p>{decision.reason}</p>
                  <div className="timeline-item__meta">
                    <span>{formatDateTime(decision.created_at)}</span>
                    <span>Действие: {decision.action_executed ? "выполнено" : "не выполнялось"}</span>
                    <span>Статус: {decision.action_status ?? "—"}</span>
                    <span>CPA: {formatMoney(decision.resolved_cpa_usd)}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </SectionCard>

        <SectionCard
          title="Правила"
          subtitle="Редактируемые stop-метрики и CPA-множители"
          actions={
            <input
              className="input input--compact"
              value={ruleSearch}
              onChange={(event) => setRuleSearch(event.target.value)}
              placeholder="Поиск по коду, названию или описанию"
            />
          }
        >
          {state.errors.rules ? <div className="inline-error">{state.errors.rules}</div> : null}
          <div className="stack" id="rules">
            {visibleRules.length === 0 ? (
              <EmptyState title="Правила не загружены" description="Список правил появится после ответа backend." />
            ) : (
              visibleRules.map((rule) => {
                const draft = {
                  title: rule.title,
                  description: rule.description ?? "",
                  is_enabled: rule.is_enabled,
                  priority: rule.priority,
                  cpa_multiplier: rule.cpa_multiplier ? String(rule.cpa_multiplier) : "",
                };
                return (
                  <RuleEditor
                    key={rule.id}
                    rule={rule}
                    draft={draft}
                    onSave={async (payload) =>
                      runAction(
                        () => saveRule(rule.id, payload),
                        `Правило ${rule.code} сохранено`,
                        "rules",
                      )
                    }
                  />
                );
              })
            )}
          </div>
        </SectionCard>

        <SectionCard
          title="Офферы"
          subtitle="Создание офферов, ставок и привязок к adset/ad"
          actions={
            <input
              className="input input--compact"
              value={offerSearch}
              onChange={(event) => setOfferSearch(event.target.value)}
              placeholder="Поиск по коду или названию"
            />
          }
        >
          {state.errors.offers ? <div className="inline-error">{state.errors.offers}</div> : null}
          <div className="offers-grid" id="offers">
            <form className="panel-form" onSubmit={(event) => void onSubmitOffer(event)}>
              <h3>Новый оффер</h3>
              <input
                className="input"
                value={offerForm.code}
                onChange={(event) => setOfferForm((current) => ({ ...current, code: event.target.value }))}
                placeholder="Код оффера"
              />
              <input
                className="input"
                value={offerForm.name}
                onChange={(event) => setOfferForm((current) => ({ ...current, name: event.target.value }))}
                placeholder="Название оффера"
              />
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={offerForm.isActive}
                  onChange={(event) => setOfferForm((current) => ({ ...current, isActive: event.target.checked }))}
                />
                <span>Оффер активен</span>
              </label>
              <button type="submit" className="button button--primary">
                Создать оффер
              </button>
            </form>

            <form className="panel-form" onSubmit={(event) => void onSubmitRate(event)}>
              <h3>Новая ставка</h3>
              <select
                className="input"
                value={rateForm.offerId}
                onChange={(event) => setRateForm((current) => ({ ...current, offerId: event.target.value }))}
              >
                <option value="">Выберите оффер</option>
                {visibleOffers.map((offer) => (
                  <option key={offer.id} value={offer.id}>
                    {offer.code} · {offer.name}
                  </option>
                ))}
              </select>
              <input
                className="input"
                value={rateForm.cpaUsd}
                onChange={(event) => setRateForm((current) => ({ ...current, cpaUsd: event.target.value }))}
                placeholder="CPA, например 5.00"
              />
              <input
                className="input"
                value={rateForm.effectiveFrom}
                onChange={(event) => setRateForm((current) => ({ ...current, effectiveFrom: event.target.value }))}
                placeholder="effective_from, например 2026-03-20T12:00:00Z"
              />
              <input
                className="input"
                value={rateForm.note}
                onChange={(event) => setRateForm((current) => ({ ...current, note: event.target.value }))}
                placeholder="Комментарий"
              />
              <button type="submit" className="button button--primary">
                Сохранить ставку
              </button>
            </form>

            <form className="panel-form panel-form--wide" onSubmit={(event) => void onSubmitBinding(event)}>
              <h3>Привязка оффера</h3>
              <div className="form-grid">
                <select
                  className="input"
                  value={bindingForm.offerId}
                  onChange={(event) => setBindingForm((current) => ({ ...current, offerId: event.target.value }))}
                >
                  <option value="">Выберите оффер</option>
                  {visibleOffers.map((offer) => (
                    <option key={offer.id} value={offer.id}>
                      {offer.code} · {offer.name}
                    </option>
                  ))}
                </select>
                <select
                  className="input"
                  value={bindingForm.targetType}
                  onChange={(event) =>
                    setBindingForm((current) => ({
                      ...current,
                      targetType: event.target.value as "adset" | "ad",
                    }))
                  }
                >
                  <option value="adset">Адсет</option>
                  <option value="ad">Объявление</option>
                </select>
              </div>
              <div className="form-grid">
                <input
                  className="input"
                  value={bindingForm.entityId}
                  onChange={(event) => setBindingForm((current) => ({ ...current, entityId: event.target.value }))}
                  placeholder="ID сущности"
                />
                <input
                  className="input"
                  value={bindingForm.priority}
                  onChange={(event) => setBindingForm((current) => ({ ...current, priority: event.target.value }))}
                  placeholder="Приоритет"
                />
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={bindingForm.isActive}
                  onChange={(event) =>
                    setBindingForm((current) => ({ ...current, isActive: event.target.checked }))
                  }
                />
                <span>Активная привязка</span>
              </label>
              <button type="submit" className="button button--primary">
                Привязать
              </button>
            </form>
          </div>

          <div className="offer-list">
            {visibleOffers.length === 0 ? (
              <EmptyState title="Офферы не загружены" description="После ответа backend появится список офферов." />
            ) : (
              visibleOffers.map((offer) => {
                return (
                  <article key={offer.id} className="offer-card">
                    <div className="offer-card__head">
                      <div>
                        <strong>{offer.name}</strong>
                        <div className="muted">{offer.code}</div>
                      </div>
                      <Badge tone={offer.is_active ? "good" : "warn"}>
                        {offer.is_active ? "активен" : "неактивен"}
                      </Badge>
                    </div>
                    <div className="offer-card__stats">
                      <span>Текущая CPA: {formatMoney(offer.current_cpa_usd)}</span>
                    </div>
                    <div className="mini-list">
                      <div className="mini-row">
                        <span>Поддержка ставок и привязок</span>
                        <span>через формы выше</span>
                      </div>
                    </div>
                  </article>
                );
              })
            )}
          </div>
        </SectionCard>

        <SectionCard
          title="Сессии browser host"
          subtitle="Активные профили, состояние attach и ручной запуск/остановка"
          actions={
            <input
              className="input input--compact"
              value={sessionSearch}
              onChange={(event) => setSessionSearch(event.target.value)}
              placeholder="Поиск по profile ID, host ID или статусу"
            />
          }
        >
          {state.errors.sessions ? <div className="inline-error">{state.errors.sessions}</div> : null}
          <div className="panel-form panel-form--inline">
            <input
              className="input"
              value={sessionReason}
              onChange={(event) => setSessionReason(event.target.value)}
              placeholder="Причина для start/stop"
            />
          </div>
          <div className="table-wrap" id="sessions">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Профиль</th>
                  <th>Хост</th>
                  <th>Статус</th>
                  <th>Attach</th>
                  <th>Последнее сообщение</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {visibleSessions.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState title="Сессий нет" description="После запуска browser host они появятся здесь." />
                    </td>
                  </tr>
                ) : (
                  visibleSessions.map((session) => (
                    <tr key={session.profile_id}>
                      <td className="mono">{session.profile_id}</td>
                      <td className="mono">{session.browser_host_id}</td>
                      <td>
                        <Badge tone={getBadgeTone(session.status)}>{formatRelativeStatus(session.status)}</Badge>
                      </td>
                      <td>
                        <div className="stack stack--tight">
                          <span>{session.cdp_url ? "CDP готов" : "CDP нет"}</span>
                          <span>{session.webdriver_url ? "WebDriver готов" : "WebDriver нет"}</span>
                        </div>
                      </td>
                      <td>{session.last_message ?? "—"}</td>
                      <td>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="button button--small"
                            onClick={() =>
                              void runAction(
                                () =>
                                  startSession({
                                    profileId: session.profile_id,
                                    browserHostId: session.browser_host_id,
                                    reason: sessionReason,
                                  }),
                                `Сессия ${session.profile_id} запущена`,
                                "sessions",
                              )
                            }
                          >
                            Запустить
                          </button>
                          <button
                            type="button"
                            className="button button--small button--ghost"
                            onClick={() =>
                              void runAction(
                                () =>
                                  stopSession({
                                    profileId: session.profile_id,
                                    browserHostId: session.browser_host_id,
                                    reason: sessionReason,
                                  }),
                                `Сессия ${session.profile_id} остановлена`,
                                "sessions",
                              )
                            }
                          >
                            Остановить
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>

      </main>
    </div>
  );
}

type RuleEditorProps = {
  rule: RuleItem;
  draft: {
    title: string;
    description: string;
    is_enabled: boolean;
    priority: number;
    cpa_multiplier: string;
  };
  onSave: (payload: Partial<RuleItem>) => Promise<void>;
};

function RuleEditor({ rule, draft, onSave }: RuleEditorProps) {
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [isEnabled, setIsEnabled] = useState(draft.is_enabled);
  const [priority, setPriority] = useState(String(draft.priority));
  const [cpaMultiplier, setCpaMultiplier] = useState(draft.cpa_multiplier);

  useEffect(() => {
    setTitle(draft.title);
    setDescription(draft.description);
    setIsEnabled(draft.is_enabled);
    setPriority(String(draft.priority));
    setCpaMultiplier(draft.cpa_multiplier);
  }, [draft.title, draft.description, draft.is_enabled, draft.priority, draft.cpa_multiplier]);

  return (
    <form
      className="rule-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSave({
          title,
          description,
          is_enabled: isEnabled,
          priority: Number(priority),
          cpa_multiplier: cpaMultiplier,
        });
      }}
    >
      <div className="rule-editor__head">
        <div>
          <strong>{rule.title}</strong>
          <div className="muted">
            {rule.code} · {formatDateTime(rule.updated_at)}
          </div>
        </div>
        <Badge tone={isEnabled ? "good" : "warn"}>{isEnabled ? "включено" : "выключено"}</Badge>
      </div>
      <div className="form-grid">
        <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
        <input
          className="input"
          value={priority}
          onChange={(event) => setPriority(event.target.value)}
          placeholder="Приоритет"
        />
      </div>
      <div className="form-grid">
        <input
          className="input"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Описание"
        />
        <input
          className="input"
          value={cpaMultiplier}
          onChange={(event) => setCpaMultiplier(event.target.value)}
          placeholder="CPA множитель"
        />
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={isEnabled} onChange={(event) => setIsEnabled(event.target.checked)} />
        <span>Правило активно</span>
      </label>
      <div className="row-actions">
        <button type="submit" className="button button--small button--primary">
          Сохранить правило
        </button>
      </div>
    </form>
  );
}
