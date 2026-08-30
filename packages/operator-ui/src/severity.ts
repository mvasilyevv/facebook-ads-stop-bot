/**
 * Общая раскраска текста/поверхностей по Severity и DataState.
 *
 * Раньше `deliveryStatusTextClass` был дословно продублирован в
 * frontend/src/features/operator/OperatorAds.tsx и
 * frontend-mini/src/features/operator/OperatorAds.tsx, а `incidentSeverityTone` —
 * в frontend/src/routes/incidents/$incidentId.tsx и
 * frontend-mini/src/routes/incidents/$incidentId.tsx. Копии не разошлись
 * семантически, но обречены были разойтись при следующей правке одного
 * файла без другого.
 */

import type { DataState, OperatorSeverity } from "@fb/shared/operator/contracts";
import { operatorDeliverySeverity } from "@fb/shared/operator/adsViewModel";

/**
 * Цвет текста по Severity: critical/warning получают семантический цвет и
 * полужирный вес, ok/unknown — нейтральный. Зелёный текст сюда не входит:
 * подтверждённый "ok" не привлекает внимание жирностью или цветом статуса.
 */
export function severityToneClass(severity: OperatorSeverity): string {
  if (severity === "critical") return "font-semibold text-danger";
  if (severity === "warning") return "font-semibold text-warning";
  return "text-bg-8";
}

/** Цвет текста статуса доставки объявления — тонкая обёртка над severityToneClass. */
export function deliveryStatusTextClass(value: string | null): string {
  return severityToneClass(operatorDeliverySeverity(value));
}

export interface IncidentSeverityTone {
  /** Классы фона + рамки карточки инцидента. */
  surface: string;
}

/**
 * Тон карточки инцидента по Severity — но только пока данные `ready`.
 *
 * `partial`, `stale` и `unavailable` никогда не выглядят зелёными и не
 * должны читаться критичнее реальной severity: любое нечестное состояние
 * данных получает нейтральную warning-поверхность, а не danger/success —
 * иначе неподтверждённый critical выглядел бы как подтверждённый, а
 * неподтверждённый ok — как разрешённый инцидент.
 */
export function incidentSeverityTone(
  severity: OperatorSeverity,
  state: DataState,
): IncidentSeverityTone {
  if (state !== "ready") {
    return { surface: "border-warning/35 bg-warning-bg" };
  }
  if (severity === "critical") {
    return { surface: "border-danger/35 bg-danger-bg" };
  }
  if (severity === "warning") {
    return { surface: "border-warning/35 bg-warning-bg" };
  }
  if (severity === "ok") {
    return { surface: "border-success/35 bg-success-bg" };
  }
  return { surface: "border-[var(--color-hairline-strong)] bg-bg-2" };
}
