/**
 * Settings (`/settings`) — конфигурация системы, 4 вкладки:
 *   1. Observer — сканирование, интервал, auto-enable, scan-runs.
 *   2. Telegram — бот, токен, recipients, invite, deep-link.
 *   3. Vision — anti-detect браузер, токен, profile, reconnect.
 *   4. Health — статусы воркеров, restart actions.
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsList, TabsContent } from "@/components/ui/Tabs";

import { ObserverTab } from "@/components/settings/ObserverTab";
import { TelegramTab } from "@/components/settings/TelegramTab";
import { VisionTab } from "@/components/settings/VisionTab";
import { HealthTab } from "@/components/settings/HealthTab";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

type SettingsTab = "observer" | "telegram" | "vision" | "health";

const TABS: { value: SettingsTab; label: string }[] = [
  { value: "observer", label: "Observer" },
  { value: "telegram", label: "Telegram" },
  { value: "vision", label: "Vision" },
  { value: "health", label: "Health" },
];

function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("observer");

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="SYSTEM"
        title="Settings."
        displayNumber="05"
        subtitle="Observer · Telegram · Vision · Health"
      />

      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as SettingsTab)}
        variant="underline"
        className="mb-8"
      >
        <TabsList items={TABS} variant="underline" className="w-full" />

        <TabsContent value="observer" className="pt-8">
          <ObserverTab />
        </TabsContent>

        <TabsContent value="telegram" className="pt-8">
          <TelegramTab />
        </TabsContent>

        <TabsContent value="vision" className="pt-8">
          <VisionTab />
        </TabsContent>

        <TabsContent value="health" className="pt-8">
          <HealthTab />
        </TabsContent>
      </Tabs>
    </>
  );
}
