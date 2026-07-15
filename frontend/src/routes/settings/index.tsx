/**
 * Settings-страница.
 * Разделы: Мониторинг / Telegram / Vision / Диагностика.
 * Диагностика объединяет runtime Observer, список воркеров и общий вердикт.
 * Каждый таб — отдельный компонент в components/settings/.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";
import { ObserverTab } from "@/components/settings/ObserverTab";
import { TelegramTab } from "@/components/settings/TelegramTab";
import { VisionTab } from "@/components/settings/VisionTab";
import { HealthTab } from "@/components/settings/HealthTab";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
  validateSearch: (search: Record<string, unknown>) => ({
    tab:
      typeof search.tab === "string" && TAB_VALUES.has(search.tab)
        ? search.tab
        : "observer",
  }),
});

const TAB_ITEMS: TabItem[] = [
  { value: "observer", label: "Мониторинг" },
  { value: "telegram", label: "Telegram" },
  { value: "vision", label: "Vision" },
  { value: "health", label: "Диагностика" },
];
const TAB_VALUES = new Set(TAB_ITEMS.map((item) => item.value));

function SettingsPage() {
  const { tab } = Route.useSearch();
  const navigate = useNavigate({ from: "/settings/" });

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="SYSTEM · КОНФИГУРАЦИЯ"
        title="Настройки"
      />

      <Tabs
        value={tab}
        onValueChange={(nextTab) => void navigate({ search: { tab: nextTab }, replace: true })}
      >
        <TabsList items={TAB_ITEMS} className="mb-8" />

        <TabsContent value="observer">
          <ObserverTab />
        </TabsContent>

        <TabsContent value="telegram">
          <TelegramTab />
        </TabsContent>

        <TabsContent value="vision">
          <VisionTab />
        </TabsContent>

        <TabsContent value="health">
          <HealthTab />
        </TabsContent>
      </Tabs>
    </>
  );
}
