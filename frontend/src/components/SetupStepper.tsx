import { Link } from "react-router-dom";
import type { OfferItem, RuleItem, BrowserSessionItem, BotModeResponse } from "../types";

type SetupStepperProps = {
  offers: OfferItem[];
  rules: RuleItem[];
  sessions: BrowserSessionItem[];
  botMode: BotModeResponse | null;
  loading: boolean;
};

type StepStatus = "pending" | "active" | "completed";
type StepDefinition = {
  number: number;
  title: string;
  description: string;
  link: string;
  actionLabel: string;
};

function getStepStatus(step: number, completed: boolean[], current: number): StepStatus {
  if (completed[step]) return "completed";
  if (step === current) return "active";
  return "pending";
}

export function SetupStepper({
  offers,
  rules,
  sessions,
  botMode,
  loading,
}: SetupStepperProps) {
  const offersExist = offers.length > 0;
  const enabledRulesExist = rules.some((r) => r.is_enabled);
  const activeSessions = sessions.filter((s) => {
    const st = s.status.toUpperCase();
    return st === "ACTIVE" || st === "STARTED";
  });
  const sessionsExist = activeSessions.length > 0;
  const botModeSet =
    botMode != null && (botMode.auto_pause_enabled || botMode.auto_resume_enabled);

  const completed = [
    offersExist,
    enabledRulesExist,
    sessionsExist,
    botModeSet,
  ];

  const currentStep = completed.findIndex((c) => !c);
  const allCompleted = completed.every((c) => c);

  if (loading || allCompleted) {
    return null;
  }

  const steps: StepDefinition[] = [
    {
      number: 1,
      title: "Создать оффер",
      description: "Создай оффер по маске из начала объявления, например `DRC_CR2` для имени `DRC_CR2_CR001`.",
      link: "/offers",
      actionLabel: "Открыть Офферы",
    },
    {
      number: 2,
      title: "Настроить правила",
      description: "На странице Правила выстави проценты от CPA через слайдеры.",
      link: "/rules",
      actionLabel: "Открыть Правила",
    },
    {
      number: 3,
      title: "Запустить браузер",
      description: "Открой Сессии и запусти рабочий браузерный профиль.",
      link: "/sessions",
      actionLabel: "Открыть Сессии",
    },
    {
      number: 4,
      title: "Настроить режим бота",
      description: "Вернись на Обзор и выбери режим наблюдения или боевой режим.",
      link: "/",
      actionLabel: "Открыть Обзор",
    },
  ];

  return (
    <section className="setup-stepper">
      <div className="setup-stepper__header">
        <h3>Первоначальная настройка</h3>
        <p className="muted">Шаг {currentStep + 1} из {steps.length}</p>
      </div>
      <div className="setup-stepper__steps">
        {steps.map((step, index) => {
          const status = getStepStatus(index, completed, currentStep);
          return (
            <div key={step.number} className={`setup-step setup-step--${status}`}>
              <div className="setup-step__number">{step.number}</div>
              <div className="setup-step__content">
                <div className="setup-step__title">{step.title}</div>
                <div className="setup-step__description">{step.description}</div>
                <div className="setup-step__status">
                  {status === "completed" && <span className="setup-step__badge">✓ Готово</span>}
                  {status === "active" && <span className="setup-step__badge setup-step__badge--active">● Текущий</span>}
                  {status === "pending" && <span className="setup-step__badge setup-step__badge--pending">○ Ожидание</span>}
                </div>
              </div>
              <Link to={step.link} className="button button--ghost button--small">
                {step.actionLabel}
              </Link>
            </div>
          );
        })}
      </div>
    </section>
  );
}
