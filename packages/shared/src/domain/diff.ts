/**
 * Построение diff-строк для UI подтверждения черновика (Drafts page).
 *
 * Контракт с бэком:
 * - DraftOut.payload содержит параметры mutation в raw-виде.
 * - DraftOut.mutation_kind определяет логику сравнения.
 * - current_state приходит от ручки GET /api/drafts/{id}/context (если есть)
 *   или передаётся UI из последнего снимка AdSnapshot.
 *
 * DiffRow — одна строка таблицы "было → станет" в модале подтверждения.
 */

export interface DiffRow {
  /** Название поля (human-readable) */
  field: string;
  /** Текущее значение (строка, "—" если неизвестно) */
  current: string;
  /** Целевое значение после мутации */
  target: string;
  /** true — значение изменится (выделяем). false — не меняется (серый). */
  changed: boolean;
}

/**
 * Payload для set_adset_budget:
 * Бюджет в центах (integer). Бэк хранит в cents, UI показывает в долларах.
 */
interface BudgetPayload {
  budget_cents?: number;
  daily_budget?: number;
  lifetime_budget?: number;
  budget_type?: "daily" | "lifetime";
}

/**
 * Payload для pause_ad / activate_ad.
 */
interface StatusPayload {
  fb_ad_id?: string;
  status?: string;
}

/**
 * Payload для bulk_status_change.
 */
interface BulkPayload {
  action?: "pause" | "activate";
  object_type?: "ad" | "campaign" | "adset";
  object_ids?: string[];
}

/** Конвертация центов в доллары для отображения. */
function centsToDisplay(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

/** Конвертация долларов (float) в отображение. */
function dollarsToDisplay(dollars: number | null | undefined): string {
  if (dollars == null) return "—";
  return `$${dollars.toFixed(2)}`;
}

/**
 * Строит массив DiffRow для данного mutation_kind.
 *
 * @param mutationKind — тип мутации из DraftOut.mutation_kind
 * @param payload — DraftOut.payload (raw объект)
 * @param currentState — снимок текущего состояния объявления/адсета (опционально).
 *   Форма зависит от mutation_kind:
 *   - set_adset_budget: { daily_budget_cents?: number; lifetime_budget_cents?: number }
 *   - pause_ad/activate_ad: { status?: string }
 *   - bulk_status_change: нет релевантного currentState (N объектов)
 */
export function buildDraftDiff(
  mutationKind: string,
  payload: Record<string, unknown> | null | undefined,
  currentState?: Record<string, unknown> | null,
): DiffRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const cs = (currentState ?? {}) as Record<string, unknown>;

  switch (mutationKind) {
    case "set_adset_budget": {
      const bp = p as BudgetPayload;
      const rows: DiffRow[] = [];
      const budgetType = bp.budget_type ?? "daily";

      if (bp.budget_cents != null) {
        // Бюджет в центах — основной формат
        const currentKey = budgetType === "daily" ? "daily_budget_cents" : "lifetime_budget_cents";
        const currentVal = cs[currentKey] as number | undefined;
        const targetDisplay = centsToDisplay(bp.budget_cents);
        const currentDisplay = centsToDisplay(currentVal);
        rows.push({
          field: budgetType === "daily" ? "Суточный бюджет" : "Бюджет (lifetime)",
          current: currentDisplay,
          target: targetDisplay,
          changed: currentVal !== bp.budget_cents,
        });
      } else if (bp.daily_budget != null) {
        // Бюджет в долларах (альтернативный формат)
        const currentVal = cs["daily_budget"] as number | undefined;
        rows.push({
          field: "Суточный бюджет",
          current: dollarsToDisplay(currentVal),
          target: dollarsToDisplay(bp.daily_budget),
          changed: currentVal !== bp.daily_budget,
        });
      } else if (bp.lifetime_budget != null) {
        const currentVal = cs["lifetime_budget"] as number | undefined;
        rows.push({
          field: "Бюджет (lifetime)",
          current: dollarsToDisplay(currentVal),
          target: dollarsToDisplay(bp.lifetime_budget),
          changed: currentVal !== bp.lifetime_budget,
        });
      }

      return rows;
    }

    case "pause_ad": {
      const sp = p as StatusPayload;
      const currentStatus = (cs["status"] as string | undefined) ?? "ACTIVE";
      return [
        {
          field: "Статус объявления",
          current: currentStatus,
          target: "PAUSED",
          changed: currentStatus !== "PAUSED",
        },
        ...(sp.fb_ad_id
          ? [
              {
                field: "Ad ID",
                current: sp.fb_ad_id,
                target: sp.fb_ad_id,
                changed: false,
              },
            ]
          : []),
      ];
    }

    case "activate_ad": {
      const sp = p as StatusPayload;
      const currentStatus = (cs["status"] as string | undefined) ?? "PAUSED";
      return [
        {
          field: "Статус объявления",
          current: currentStatus,
          target: "ACTIVE",
          changed: currentStatus !== "ACTIVE",
        },
        ...(sp.fb_ad_id
          ? [
              {
                field: "Ad ID",
                current: sp.fb_ad_id,
                target: sp.fb_ad_id,
                changed: false,
              },
            ]
          : []),
      ];
    }

    case "bulk_status_change": {
      const bp = p as BulkPayload;
      const ids = bp.object_ids ?? [];
      const action = bp.action === "activate" ? "ACTIVE" : "PAUSED";
      const objectType = bp.object_type ?? "ad";
      return [
        {
          field: "Тип объектов",
          current: objectType,
          target: objectType,
          changed: false,
        },
        {
          field: "Количество объектов",
          current: "—",
          target: String(ids.length),
          changed: ids.length > 0,
        },
        {
          field: "Целевой статус",
          current: "—",
          target: action,
          changed: true,
        },
      ];
    }

    case "pause_campaign":
    case "activate_campaign": {
      const targetStatus = mutationKind === "activate_campaign" ? "ACTIVE" : "PAUSED";
      const currentStatus = (cs["status"] as string | undefined) ?? (mutationKind === "activate_campaign" ? "PAUSED" : "ACTIVE");
      return [
        {
          field: "Статус кампании",
          current: currentStatus,
          target: targetStatus,
          changed: currentStatus !== targetStatus,
        },
      ];
    }

    case "duplicate_campaign": {
      const campaignName = p["campaign_name"] as string | undefined;
      return [
        {
          field: "Новое имя кампании",
          current: "—",
          target: campaignName ?? "(будет назначено)",
          changed: true,
        },
      ];
    }

    default:
      // Для неизвестных mutation_kind показываем raw-payload как JSON
      return [
        {
          field: "Параметры",
          current: "—",
          target: JSON.stringify(p, null, 2),
          changed: true,
        },
      ];
  }
}
