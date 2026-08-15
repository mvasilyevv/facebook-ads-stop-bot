export function russianCountForm(count: number, one: string, few: string, many: string): string {
  const absolute = Math.abs(count);
  const lastTwo = absolute % 100;
  const last = absolute % 10;
  if (last === 1 && lastTwo !== 11) return one;
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return few;
  return many;
}

export function formatRussianCount(count: number, one: string, few: string, many: string): string {
  return `${count.toLocaleString("ru-RU")} ${russianCountForm(count, one, few, many)}`;
}

export function russianCountIsOne(count: number): boolean {
  const absolute = Math.abs(count);
  return absolute % 10 === 1 && absolute % 100 !== 11;
}
