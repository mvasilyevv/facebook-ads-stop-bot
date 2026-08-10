const RFC3339_TIMESTAMP =
  /^((?!0000)\d{4})-(0[1-9]|1[0-2])-(\d{2})T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.\d+)?(?:Z|[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00))$/;

/** Strict server timestamp: full RFC3339 date-time with an explicit offset. */
export function isRfc3339Timestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = RFC3339_TIMESTAMP.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  return day >= 1 && day <= daysInMonth(year, month);
}

/** Both endpoints must be strict timestamps and the interval must be positive. */
export function isIncreasingTimestampRange(
  from: unknown,
  to: unknown,
): boolean {
  return (
    isRfc3339Timestamp(from) &&
    isRfc3339Timestamp(to) &&
    Date.parse(from) < Date.parse(to)
  );
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) return isLeapYear(year) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}
