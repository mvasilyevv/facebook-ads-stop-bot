import type { OfferItem, OfferBindingItem, RuleItem, BrowserSessionItem, BotModeResponse } from "../types";

type SetupStepperProps = {
  offers: OfferItem[];
  bindings: OfferBindingItem[];
  rules: RuleItem[];
  sessions: BrowserSessionItem[];
  botMode: BotModeResponse | null;
  loading: boolean;
};

type StepStatus = "pending" | "active" | "completed";

function getStepStatus(step: number, completed: boolean[], current: number): StepStatus {
  if (completed[step]) return "completed";
  if (step === current) return "active";
  return "pending";
}

export function SetupStepper({
  offers,
  bindings,
  rules,
  sessions,
  botMode,
  loading,
}: SetupStepperProps) {
  // Проверяем условия завершения для каждого шага
  const offersExist = offers.length > 0;
  const bindingsExist = bindings.length > 0;
  const enabledRulesExist = rules.some((r) => r.is_enabled);
  const activeSessions = sessions.filter((s) => s.status === "active" || s.status === "started");
  const sessionsExist = activeSessions.length > 0;
  const botModeSet =
    botMode != null && (botMode.auto_pause_enabled || botMode.auto_resume_enabled);

  const completed = [
    offersExist,
    bindingsExist,
    enabledRulesExist,
    sessionsExist,
    botModeSet,
  ];

  const currentStep = completed.findIndex((c) => !c);
  const allCompleted = completed.every((c) => c);

  if (loading || allCompleted) {
    return null;
  }

  const steps = [
    {
      number: 1,
      title: "Создать предложение",
      link: "/offers",
    },
    {
      number: 2,
      title: "Привязать предложение",
      link: "/offers",
    },
    {
      number: 3,
      title: "Настроить правила",
      link: "/rules",
    },
    {
      number: 4,
      title: "Запустить браузер",
      link: "/sessions",
    },
    {
      number: 5,
      title: "Настроить режим бота",
      link: "#",
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
                <div className="setup-step__status">
                  {status === "completed" && <span className="setup-step__badge">✓ Готово</span>}
                  {status === "active" && <span className="setup-step__badge setup-step__badge--active">● Текущий</span>}
                  {status === "pending" && <span className="setup-step__badge setup-step__badge--pending">○ Ожидание</span>}
                </div>
              </div>
              {step.link !== "#" && (
                <a href={step.link} className="button button--ghost button--small">
                  Перейти
                </a>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
