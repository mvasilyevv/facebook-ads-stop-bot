/**
 * Helper для тестов шагов визарда.
 * Минималистичная версия компонентов шагов, пригодная для unit-тестирования
 * без зависимости от Router/реальных API-хуков.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

export function TestProviders({ children }: { children: React.ReactNode }) {
  const qc = makeQC();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
