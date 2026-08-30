/**
 * useTelegramMainButton — тонкая обвязка над Telegram MainButton.
 *
 * Fail-closed: если WebApp API недоступен (обычный браузер, старый клиент,
 * тесты вне Telegram) — хук ничего не делает и возвращает `available: false`.
 * Вызывающий компонент обязан в этом случае отрендерить свою обычную кнопку.
 */
import { useEffect, useRef } from "react";
import { getMainButton } from "@/lib/tg";

export interface UseTelegramMainButtonOptions {
  /** Текст кнопки. */
  text: string;
  /** Обработчик подтверждающего действия. */
  onClick: () => void;
  /** Показывать ли кнопку сейчас (например, скрыть на промежуточном экране). */
  visible?: boolean;
  /** Видна, но недоступна к нажатию. */
  disabled?: boolean;
  /** Идёт операция — показывает встроенный спиннер вместо текста. */
  loading?: boolean;
}

export interface UseTelegramMainButtonResult {
  /** true, если Telegram MainButton реально управляется этим хуком. */
  available: boolean;
}

export function useTelegramMainButton(
  options: UseTelegramMainButtonOptions,
): UseTelegramMainButtonResult {
  const button = getMainButton();
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  // Обработчик клика регистрируется один раз на время жизни кнопки — без
  // этого пришлось бы снимать/навешивать onClick при каждом ре-рендере.
  useEffect(() => {
    if (!button) return undefined;
    const handleClick = () => optionsRef.current.onClick();
    button.onClick(handleClick);
    return () => {
      button.offClick(handleClick);
      button.hide();
    };
  }, [button]);

  const { text, visible = true, disabled = false, loading = false } = options;

  useEffect(() => {
    if (!button) return;
    button.setText(text);
    if (!visible) {
      button.hide();
      return;
    }
    button.show();
    if (disabled) button.disable?.();
    else button.enable?.();
    if (loading) button.showProgress?.(false);
    else button.hideProgress?.();
  }, [button, text, visible, disabled, loading]);

  return { available: Boolean(button) };
}
