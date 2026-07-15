/**
 * AITab — статус AI-провайдеров.
 * Провайдеры настраиваются через .env, поэтому нет формы.
 * Только информационный экран.
 */

import { type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Bot } from "lucide-react";

export const AITab: FC = () => {
  return (
    <div className="space-y-5 max-w-xl">
      <Card eyebrow="AI-провайдеры" padded>
        <div className="flex items-start gap-4">
          <div className="text-bg-8 mt-0.5 shrink-0" aria-hidden="true">
            <Bot size={24} />
          </div>
          <div>
            <div className="font-display text-[14px] text-bg-11 mb-2">
              Настраивается через .env
            </div>
            <div className="font-display text-[12px] text-bg-9 space-y-1.5">
              <p>AI-провайдеры (Anthropic, OpenAI) настраиваются через переменные окружения:</p>
              <ul className="ml-4 space-y-1 list-disc list-outside text-bg-8">
                <li><code className="text-accent">ANTHROPIC_API_KEY</code> — Anthropic Claude</li>
                <li><code className="text-accent">OPENAI_API_KEY</code> — OpenAI GPT</li>
                <li><code className="text-accent">ANTHROPIC_BASE_URL</code> — прокси (необязательно)</li>
              </ul>
              <p className="mt-2 text-bg-8">
                После добавления ключей перезапустите backend API.
                AI-анализ становится доступен через <code className="text-accent">POST /api/ai/analyze</code>.
              </p>
            </div>
          </div>
        </div>
      </Card>

      <Card eyebrow="Rate limits" padded>
        <div className="space-y-2">
          {[
            { label: "Глобальный rate-limit", value: "30 запросов / час" },
            { label: "AI analyze rate-limit", value: "20 запросов / час (per IP)" },
            { label: "Кэш AI-ответов", value: "600 секунд (Redis)" },
          ].map(({ label, value }) => (
            <div key={label} className="flex items-baseline justify-between gap-2 py-1.5 border-b border-[var(--hairline)] last:border-b-0">
              <span className="font-display text-[11px] uppercase tracking-[0.08em] text-bg-8 shrink-0">
                {label}
              </span>
              <span className="font-display text-[13px] text-bg-10 text-right">{value}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
