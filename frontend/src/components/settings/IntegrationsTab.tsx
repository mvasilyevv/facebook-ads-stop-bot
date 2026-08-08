import { TelegramTab } from "./TelegramTab";
import { VisionTab } from "./VisionTab";

export function IntegrationsTab() {
  return (
    <div className="flex flex-col gap-10">
      <section>
        <div className="mb-4 font-display text-[12px] uppercase tracking-[0.1em] text-bg-8">
          Telegram
        </div>
        <TelegramTab />
      </section>
      <section className="border-t border-[var(--color-hairline)] pt-8">
        <div className="mb-4 font-display text-[12px] uppercase tracking-[0.1em] text-bg-8">
          Vision / Ads Manager
        </div>
        <VisionTab />
      </section>
    </div>
  );
}
