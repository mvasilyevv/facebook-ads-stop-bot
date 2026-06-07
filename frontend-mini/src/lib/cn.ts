/**
 * cn — объединение className со слиянием Tailwind-классов.
 * Комбинирует clsx (условные строки) + tailwind-merge (дедуп конфликтующих утилит).
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
