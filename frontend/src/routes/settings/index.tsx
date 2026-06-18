/**
 * Settings-страница.
 * Tabs: Observer / Telegram / Vision / Health.
 * Health объединяет бывший Workers (Observer Runtime + список воркеров) с вердиктом.
 * Каждый таб — отдельный компонент в components/settings/.
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";
import { ObserverTab } from "@/components/settings/ObserverTab";
import { TelegramTab } from "@/components/settings/TelegramTab";
import { VisionTab } from "@/components/settings/VisionTab";
import { HealthTab } from "@/components/settings/HealthTab";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

const TAB_ITEMS: TabItem[] = [
  { value: "observer", label: "Observer" },
  { value: "telegram", label: "Telegram" },
  { value: "vision", label: "Vision" },
  { value: "health", label: "Health" },
];

function SettingsPage() {
  const [tab, setTab] = useState("observer");

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="SYSTEM · КОНФИГУРАЦИЯ"
        title="Настройки"
      />

      <Tabs value={tab} onValueChange={setTab}>
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
