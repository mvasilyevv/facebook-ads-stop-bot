/**
 * Хелперы для форматирования Meta-идентификаторов.
 * Meta ad/campaign ID — длинные числовые строки (16-18 цифр).
 */

/**
 * Сокращает длинный Meta-ID для отображения: "120211...8761".
 * @param adId — полный ID (строка).
 * @param headLen — сколько символов сначала (default 6).
 * @param tailLen — сколько символов в конце (default 4).
 */
export function truncateAdId(
  adId: string | null | undefined,
  headLen = 6,
  tailLen = 4,
): string {
  if (!adId) return "—";
  if (adId.length <= headLen + tailLen + 3) return adId;
  return `${adId.slice(0, headLen)}...${adId.slice(-tailLen)}`;
}
