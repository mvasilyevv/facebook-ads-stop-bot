/**
 * OfferFormModal — модал создания/редактирования оффера + money-настроек.
 *
 * Поля:
 *   - code (string, только при создании; name=code на бэке)
 *   - ad_account_ids (мульти-кабинет, минимум 1)
 *   - is_active (boolean)
 *   - rules: CPA + ползунки stop%/warning% + live-разбивка (OfferRulesFields)
 *
 * vertical убран из UI (на матчинг/правила не влияет; колонка в БД остаётся nullable).
 * Сохранение правил — отдельным PUT /offers/{id}/rules, проводку делает родитель
 * (routes/offers): create → id → rules, либо update → rules.
 */

import { useEffect, useState } from "react";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import {
  OfferRulesFields,
  DEFAULT_OFFER_RULES_VALUES,
  type OfferRulesValues,
} from "./OfferRulesFields";
import type { Offer } from "@fb/shared";

// ─── Типы ────────────────────────────────────────────────────────────────────

export interface OfferFormValues {
  code: string;
  is_active: boolean;
  /** Мульти-кабинет: числовые ID кабинетов (без act_), минимум 1. */
  ad_account_ids: string[];
  /** Money-настройки: CPA + чувствительность стоп/warning. */
  rules: OfferRulesValues;
}

/** Разбор ввода кабинетов: запятые/пробелы/переносы, срез act_, дедуп. */
function parseAccountIds(raw: string): { ids: string[]; invalid: string[] } {
  const ids: string[] = [];
  const invalid: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(/[\s,;]+/)) {
    const token = part.trim();
    if (!token) continue;
    const normalized = token.replace(/^act_/i, "");
    if (!/^\d+$/.test(normalized)) {
      invalid.push(token);
      continue;
    }
    if (!seen.has(normalized)) {
      seen.add(normalized);
      ids.push(normalized);
    }
  }
  return { ids, invalid };
}

interface OfferFormModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Если задан — режим редактирования. Иначе — создание. */
  offer?: Offer | null;
  /** Текущие правила оффера (для режима редактирования). */
  initialRules?: Partial<OfferRulesValues>;
  /** Обработчик сохранения. Получает поля формы + правила. */
  onSave: (values: OfferFormValues) => Promise<void>;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferFormModal({
  open,
  onOpenChange,
  offer,
  initialRules,
  onSave,
}: OfferFormModalProps) {
  const isEdit = !!offer;
  // ad_account_ids появляется в generated-типах после pnpm gen:api — до этого читаем мягко.
  const offerAccounts =
    (offer as (Offer & { ad_account_ids?: string[] }) | null | undefined)?.ad_account_ids ?? [];

  const [code, setCode] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [rules, setRules] = useState<OfferRulesValues>(DEFAULT_OFFER_RULES_VALUES);
  // Сырой ввод кабинетов (текст до парсинга) — парсим на submit.
  const [accountsRaw, setAccountsRaw] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>();
  const [accountsError, setAccountsError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);

  // Синхронизируем состояние при открытии/смене оффера/правил.
  useEffect(() => {
    if (open) {
      setCode(offer?.code ?? "");
      setIsActive(offer?.is_active ?? true);
      setRules({ ...DEFAULT_OFFER_RULES_VALUES, ...initialRules });
      setAccountsRaw(offerAccounts.join(", "));
      setCodeError(undefined);
      setAccountsError(undefined);
    }
    // offerAccounts/initialRules — производные от offer; отдельные зависимости не нужны.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, offer, initialRules]);

  function handleClose(next: boolean) {
    if (busy) return;
    onOpenChange(next);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    // Валидация кода (только при создании).
    if (!isEdit) {
      const trimmed = code.trim();
      if (!trimmed) {
        setCodeError("Код оффера обязателен");
        return;
      }
      if (!/^[A-Z0-9_]+$/i.test(trimmed)) {
        setCodeError("Только латиница, цифры и _ (напр. CR2, GH_AVI)");
        return;
      }
    }

    // Валидация кабинетов: минимум 1, только числовые ID (мульти-кабинет).
    const { ids, invalid } = parseAccountIds(accountsRaw);
    if (invalid.length > 0) {
      setAccountsError(`Не похоже на ID кабинета: ${invalid.join(", ")}`);
      return;
    }
    if (ids.length === 0) {
      setAccountsError("Укажи минимум один ID кабинета — без него оффер не сканируется");
      return;
    }

    setBusy(true);
    try {
      await onSave({
        code: code.trim().toUpperCase(),
        is_active: isActive,
        ad_account_ids: ids,
        rules,
      });
      handleClose(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={handleClose}
      title={isEdit ? `Оффер ${offer!.code}` : "Новый оффер"}
      description={
        isEdit ? "Кабинеты, статус и стоп-правила." : "Добавить оффер для матчинга кампаний."
      }
      size="md"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <div className="flex flex-col gap-4 mb-6">
          {/* Код оффера — только при создании */}
          {!isEdit ? (
            <Input
              id="offer-code"
              label="Код оффера"
              placeholder="CR2"
              value={code}
              onChange={(e) => {
                setCode(e.target.value);
                if (codeError) setCodeError(undefined);
              }}
              errorMessage={codeError}
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
          ) : (
            <div>
              <div className="font-display text-[11px] tracking-wider uppercase text-bg-9 mb-1.5">
                Код оффера
              </div>
              <div className="font-display text-[14px] text-accent tracking-[0.06em]">
                {offer!.code}
                <span className="ml-2 text-[10px] text-bg-8 tracking-normal normal-case">
                  (нельзя изменить)
                </span>
              </div>
            </div>
          )}

          {/* Кабинеты (мульти-кабинет): числовые ID через запятую/пробел, минимум 1 */}
          <Input
            id="offer-accounts"
            label="Рекламные кабинеты"
            placeholder="1234567890, 9876543210"
            value={accountsRaw}
            onChange={(e) => {
              setAccountsRaw(e.target.value);
              if (accountsError) setAccountsError(undefined);
            }}
            errorMessage={accountsError}
            autoComplete="off"
            spellCheck={false}
            helpText="ID кабинетов, где крутится оффер (без act_). Сканируются только кабинеты, указанные хотя бы у одного активного оффера."
          />

          {/* ── Money-настройки: CPA + чувствительность + live-разбивка ── */}
          <div className="pt-2 border-t border-[var(--hairline)]">
            <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 mb-3">
              СТОП-ПРАВИЛА
            </div>
            <OfferRulesFields
              values={rules}
              onChange={(patch) => setRules((r) => ({ ...r, ...patch }))}
              disabled={busy}
            />
          </div>

          {/* Статус */}
          <Switch
            checked={isActive}
            onChange={() => setIsActive((v) => !v)}
            label="Активный оффер"
            visualLabel="Статус"
            description="Неактивные офферы не матчатся с кампаниями и скрыты из дашборда."
          />
        </div>

        <ModalFooter>
          <Button type="button" variant="ghost" onClick={() => handleClose(false)} disabled={busy}>
            Отмена
          </Button>
          <Button type="submit" variant="primary" loading={busy}>
            {isEdit ? "Сохранить" : "Создать оффер"}
          </Button>
        </ModalFooter>
      </form>
    </Modal>
  );
}
