/**
 * OfferFormModal — модал создания/редактирования оффера.
 *
 * Поля (по спеке бэка offers.py:244, code immutable при редактировании):
 *   - code (string, только при создании)
 *   - vertical (string, опционально)
 *   - is_active (boolean)
 *
 * Поле «Название» (name) отсутствует в UI — бэк принимает name=code.
 * CLAUDE.md: «name=code, поле «Название» убрано из UI»
 */

import { useEffect, useState } from "react";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import type { Offer } from "@fb/shared";

// ─── Вертикали (предустановленные варианты) ───────────────────────────────────

const VERTICAL_OPTIONS = [
  { value: "", label: "— не указана —" },
  { value: "gambling", label: "Gambling" },
  { value: "betting", label: "Betting" },
  { value: "nutra", label: "Nutra" },
  { value: "crypto", label: "Crypto" },
  { value: "dating", label: "Dating" },
  { value: "finance", label: "Finance" },
  { value: "other", label: "Other" },
];

// ─── Типы ────────────────────────────────────────────────────────────────────

interface OfferFormValues {
  code: string;
  vertical: string;
  is_active: boolean;
  /** Мульти-кабинет: числовые ID кабинетов (без act_), минимум 1. */
  ad_account_ids: string[];
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
  /** Обработчик сохранения. Получает заполненные поля формы. */
  onSave: (values: OfferFormValues) => Promise<void>;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferFormModal({ open, onOpenChange, offer, onSave }: OfferFormModalProps) {
  const isEdit = !!offer;
  // ad_account_ids появляется в generated-типах после pnpm gen:api — до этого читаем мягко.
  const offerAccounts =
    (offer as (Offer & { ad_account_ids?: string[] }) | null | undefined)?.ad_account_ids ?? [];

  const [values, setValues] = useState<OfferFormValues>({
    code: "",
    vertical: "",
    is_active: true,
    ad_account_ids: [],
  });
  // Сырой ввод кабинетов (текст до парсинга) — парсим на submit и on-blur.
  const [accountsRaw, setAccountsRaw] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>();
  const [accountsError, setAccountsError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);

  // Синхронизируем состояние при открытии/смене оффера
  useEffect(() => {
    if (open) {
      setValues({
        code: offer?.code ?? "",
        vertical: offer?.vertical ?? "",
        is_active: offer?.is_active ?? true,
        ad_account_ids: offerAccounts,
      });
      setAccountsRaw(offerAccounts.join(", "));
      setCodeError(undefined);
      setAccountsError(undefined);
    }
    // offerAccounts — производное от offer, отдельная зависимость не нужна.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, offer]);

  function handleClose(next: boolean) {
    if (busy) return;
    onOpenChange(next);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    // Валидация кода
    if (!isEdit) {
      const code = values.code.trim();
      if (!code) {
        setCodeError("Код оффера обязателен");
        return;
      }
      if (!/^[A-Z0-9_]+$/i.test(code)) {
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
        ...values,
        code: values.code.trim().toUpperCase(),
        ad_account_ids: ids,
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
      description={isEdit ? "Изменить вертикаль и статус." : "Добавить оффер для матчинга кампаний."}
      size="sm"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <div className="flex flex-col gap-4 mb-6">
          {/* Код оффера — только при создании */}
          {!isEdit ? (
            <Input
              id="offer-code"
              label="Код оффера"
              placeholder="CR2"
              value={values.code}
              onChange={(e) => {
                setValues((v) => ({ ...v, code: e.target.value }));
                if (codeError) setCodeError(undefined);
              }}
              errorMessage={codeError}
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
          ) : (
            /* В режиме редактирования — readonly code badge */
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

          {/* Вертикаль */}
          <Select
            id="offer-vertical"
            label="Вертикаль"
            options={VERTICAL_OPTIONS}
            value={values.vertical}
            onChange={(e) => setValues((v) => ({ ...v, vertical: e.target.value }))}
          />

          {/* Статус */}
          <Switch
            checked={values.is_active}
            onChange={() => setValues((v) => ({ ...v, is_active: !v.is_active }))}
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
