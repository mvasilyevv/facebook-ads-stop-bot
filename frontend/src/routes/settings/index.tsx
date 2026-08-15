/**
 * Settings-страница.
 * Разделы: отображение, автоматизация и интеграции.
 * Runtime-состояние находится на канонической странице `/system/sources`.
 * Каждый таб — отдельный компонент в components/settings/.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";
import { ObserverTab } from "@/components/settings/ObserverTab";
import { DisplayTab } from "@/components/settings/DisplayTab";
import { IntegrationsTab } from "@/components/settings/IntegrationsTab";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
  validateSearch: (search: Record<string, unknown>) => ({
    tab: typeof search.tab === "string" && TAB_VALUES.has(search.tab) ? search.tab : "display",
  }),
});

const TAB_ITEMS: TabItem[] = [
  { value: "display", label: "Отображение" },
  { value: "automation", label: "Автоматизация" },
  { value: "integrations", label: "Интеграции" },
];
const TAB_VALUES = new Set(TAB_ITEMS.map((item) => item.value));

function SettingsPage() {
  const { tab } = Route.useSearch();
  const navigate = useNavigate({ from: "/settings/" });

  return (
    <>
      <PageHeader eyebrowNum="04" eyebrow="СИСТЕМА · НАСТРОЙКИ" title="Настройки" />

      <Tabs
        value={tab}
        onValueChange={(nextTab) => void navigate({ search: { tab: nextTab }, replace: true })}
      >
        <TabsList items={TAB_ITEMS} className="mb-8" />

        <TabsContent value="display">
          <DisplayTab />
        </TabsContent>

        <TabsContent value="automation">
          <ObserverTab />
        </TabsContent>

        <TabsContent value="integrations">
          <IntegrationsTab />
        </TabsContent>
      </Tabs>
    </>
  );
}
