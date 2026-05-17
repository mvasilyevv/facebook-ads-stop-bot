export const Currency = {
  USD: 'USD',
  EUR: 'EUR',
  RUB: 'RUB',
  UAH: 'UAH',
} as const;
export type Currency = (typeof Currency)[keyof typeof Currency];
