/**
 * OfferFormModal — модал создания/редактирования оффера (только identity).
 *
 * Поля:
 *   - code (string, только при создании; name=code на бэке)
 *   - ad_account_ids (мульти-кабинет, минимум 1)
 *   - pixel_id, countries (гео) — для дерайва визарда
 *   - is_active (boolean)
 *
 * Стоп-правила (CPA + чувствительность) — НЕ здесь: они в отдельной кнопке «Правила»
 * (RulesDrawer). Целевой CPA един и живёт в правилах (offer_rules.cpa_threshold) —
 * из него и пороги, и префилл бида визарда. vertical убран из UI (колонка nullable).
 */

import { useEffect, useState } from "react";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { TagListInput } from "@/components/ui/TagListInput";
import { Switch } from "@/components/ui/Switch";
import type { Offer } from "@fb/shared";

// ─── Типы ────────────────────────────────────────────────────────────────────

export interface OfferFormValues {
  code: string;
  is_active: boolean;
  /** FB Pixel ID оффера (событие оптимизации Purchase/FTD). Пусто — не задан. */
  pixel_id: string;
  /** Мульти-кабинет: числовые ID кабинетов (без act_), минимум 1. */
  ad_account_ids: string[];
  /** Гео оффера (ISO-2 upper). Дефолт [] — не задано. */
  countries: string[];
}

// Кабинет: срез act_ и проверка на числовой ID — для TagListInput.
const normalizeAccount = (token: string): string => token.replace(/^act_/i, "");
const validateAccount = (token: string): string | null =>
  /^\d+$/.test(token) ? null : "только числовой ID кабинета";

// Гео: upper-case ISO-2; проверка на ровно две латинские буквы.
const normalizeCountry = (token: string): string => token.trim().toUpperCase();
const validateCountry = (token: string): string | null =>
  /^[A-Z]{2}$/.test(token) ? null : "только ISO-2 код (напр. DE, BR)";

interface OfferFormModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Если задан — режим редактирования. Иначе — создание. */
  offer?: Offer | null;
  /** Обработчик сохранения. Получает identity-поля оффера. */
  onSave: (values: OfferFormValues) => Promise<void>;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function OfferFormModal({ open, onOpenChange, offer, onSave }: OfferFormModalProps) {
  const isEdit = !!offer;
  // Offer из @fb/shared не содержит countries (gen:api не запускаем) — читаем мягко
  // через расширение. ad_account_ids/pixel_id уже есть в generated.
  const offerExt = offer as
    | (Offer & {
        ad_account_ids?: string[];
        pixel_id?: string | null;
        countries?: string[];
      })
    | null
    | undefined;
  const offerAccounts = offerExt?.ad_account_ids ?? [];
  const offerCountries = offerExt?.countries ?? [];

  const [code, setCode] = useState("");
  const [isActive, setIsActive] = useState(true);
  // Кабинеты как список тэгов (без сырой строки) — добавление/удаление поэлементно.
  const [accounts, setAccounts] = useState<string[]>([]);
  // FB Pixel ID оффера.
  const [pixelId, setPixelId] = useState("");
  // Гео оффера (ISO-2 upper) тэгами.
  const [countries, setCountries] = useState<string[]>([]);
  const [codeError, setCodeError] = useState<string | undefined>();
  const [accountsError, setAccountsError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);

  // Синхронизируем состояние при открытии/смене оффера.
  useEffect(() => {
    if (open) {
      setCode(offer?.code ?? "");
      setIsActive(offer?.is_active ?? true);
      setAccounts(offerAccounts);
      setPixelId(offerExt?.pixel_id ?? "");
      setCountries(offerCountries);
      setCodeError(undefined);
      setAccountsError(undefined);
    }
    // offerAccounts/offerCountries — производные от offer; отдельные зависимости не нужны.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, offer]);

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

    // Кабинеты валидируются поэлементно в TagListInput — здесь проверяем только «минимум 1».
    if (accounts.length === 0) {
      setAccountsError("Укажи минимум один ID кабинета — без него оффер не сканируется");
      return;
    }

    setBusy(true);
    try {
      await onSave({
        code: code.trim().toUpperCase(),
        is_active: isActive,
        pixel_id: pixelId.trim(),
        ad_account_ids: accounts,
        countries,
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
        isEdit
          ? "Кабинеты, пиксель, гео, статус. Стоп-правила — в кнопке «Правила»."
          : "Добавить оффер для матчинга кампаний. Стоп-правила — потом в «Правилах»."
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

          {/* Кабинеты (мульти-кабинет): тэги — добавляешь по одному, минимум 1 */}
          <TagListInput
            id="offer-accounts"
            label="Рекламные кабинеты"
            placeholder="1234567890 + Enter"
            values={accounts}
            onChange={(next) => {
              setAccounts(next);
              if (accountsError) setAccountsError(undefined);
            }}
            normalize={normalizeAccount}
            validate={validateAccount}
            errorMessage={accountsError}
            disabled={busy}
            helpText="ID кабинетов, где крутится оффер (без act_). Enter/запятая — добавить, × — удалить. Сканируются только кабинеты, указанные хотя бы у одного активного оффера."
          />

          {/* FB Pixel ID — событие оптимизации при создании кампаний */}
          <Input
            id="offer-pixel"
            label="FB Pixel ID"
            placeholder="1234567890123456"
            value={pixelId}
            onChange={(e) => setPixelId(e.target.value)}
            disabled={busy}
            autoComplete="off"
            spellCheck={false}
            inputMode="numeric"
            helpText="Пиксель оффера — событие оптимизации (Purchase/FTD) при создании кампаний. Необязательно."
          />

          {/* Гео (страны) — тэги ISO-2; префилл geo визарда при создании кампаний */}
          <TagListInput
            id="offer-countries"
            label="Страны (гео)"
            placeholder="DE + Enter"
            values={countries}
            onChange={setCountries}
            normalize={normalizeCountry}
            validate={validateCountry}
            disabled={busy}
            helpText="ISO-2 коды (DE, BR, IN). Enter/запятая — добавить, × — удалить. Подставляются в гео при создании кампаний. Необязательно."
          />

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
