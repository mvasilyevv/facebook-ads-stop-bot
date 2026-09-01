/**
 * CommandPalette (⌘K) — быстрая навигация и поиск.
 *
 * - Открытие: ⌘K / Ctrl+K (глобальный listener) или клик по SearchTrigger в TopBar.
 * - Источники: статичные разделы + офферы + серверный typed-поиск `/operator/ads`.
 * - Клавиатура: ↑/↓ перемещение, Enter — переход, Esc — закрыть (Radix).
 *
 * Рендерится один раз в Shell (всегда смонтирован — отсюда глобальный keydown).
 */
import * as Dialog from "@radix-ui/react-dialog";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import {
  Search,
  LayoutDashboard,
  Activity,
  ListChecks,
  ShieldAlert,
  Table,
  Radar,
  Package,
  BarChart3,
  MonitorUp,
  Settings,
} from "lucide-react";

import { useCommandPalette } from "@/stores/commandPalette";
import { useOffers } from "@/lib/api/offers";
import { useOperatorAds } from "@/lib/api/operator";
import { cn } from "@/lib/utils/cn";
import { truncateAdId } from "@fb/shared";

interface CommandItem {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon?: React.ReactNode;
  run: () => void;
}

const PAGES = [
  {
    to: "/",
    label: "Обзор",
    kw: "dashboard панель главная обзор",
    icon: <LayoutDashboard size={15} />,
  },
  {
    to: "/decisions",
    label: "Решения",
    kw: "decisions решения лента очередь attention внимание",
    icon: <ListChecks size={15} />,
  },
  {
    to: "/actions",
    label: "Действия",
    kw: "actions действия команды очередь pause activate",
    icon: <Activity size={15} />,
  },
  {
    to: "/incidents",
    label: "Инциденты",
    kw: "incidents инциденты алерты предупреждения критично",
    icon: <ShieldAlert size={15} />,
  },
  { to: "/ads", label: "Объявления", kw: "ads объявления реклама", icon: <Table size={15} /> },
  {
    to: "/campaigns",
    label: "Кампании",
    kw: "campaigns кампании скоуп owner tag отслеживаемые",
    icon: <Radar size={15} />,
  },
  { to: "/offers", label: "Офферы", kw: "offers офферы", icon: <Package size={15} /> },
  {
    to: "/analytics",
    label: "Аналитика",
    kw: "analytics статистика история события заливы",
    icon: <BarChart3 size={15} />,
  },
  {
    to: "/remote-desktop",
    label: "Рабочий стол",
    kw: "remote desktop рабочий стол vision сервер",
    icon: <MonitorUp size={15} />,
  },
  {
    to: "/settings",
    label: "Настройки",
    kw: "settings настройки конфиг",
    icon: <Settings size={15} />,
  },
] as const;

interface CommandPaletteProps {
  /** Shell owns the shortcut when the palette itself is lazy-mounted. */
  manageShortcut?: boolean;
}

export function CommandPalette({ manageShortcut = true }: CommandPaletteProps) {
  const { open, setOpen, toggle } = useCommandPalette();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const q = query.trim().toLowerCase();

  // Глобальная горячая клавиша ⌘K / Ctrl+K.
  useEffect(() => {
    if (!manageShortcut) return;
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [manageShortcut, toggle]);

  // Данные для поиска (включаются только при открытой палитре).
  const offersQ = useOffers();
  const adsQ = useOperatorAds(
    { search: q.length >= 2 ? q : undefined, page: 1, page_size: 10 },
    { enabled: open && q.length >= 2 },
  );

  const items = useMemo<CommandItem[]>(() => {
    const list: CommandItem[] = [];

    for (const p of PAGES) {
      if (!q || p.label.toLowerCase().includes(q) || p.kw.includes(q)) {
        list.push({
          id: `page:${p.to}`,
          label: p.label,
          group: "Разделы",
          icon: p.icon,
          run: () => {
            navigate({ to: p.to });
            setOpen(false);
          },
        });
      }
    }

    if (q.length >= 2) {
      const offers = (offersQ.data ?? [])
        .filter(
          (o) =>
            (o.code ?? "").toLowerCase().includes(q) || (o.name ?? "").toLowerCase().includes(q),
        )
        .slice(0, 5);
      for (const o of offers) {
        list.push({
          id: `offer:${o.id}`,
          label: o.code ?? o.name ?? "—",
          hint: "оффер",
          group: "Офферы",
          icon: <Package size={15} />,
          run: () => {
            navigate({ to: "/offers" });
            setOpen(false);
          },
        });
      }

      const ads = (adsQ.data?.rows ?? []).slice(0, 6);
      for (const a of ads) {
        list.push({
          id: `ad:${a.fb_ad_id}`,
          label: a.name || truncateAdId(a.fb_ad_id),
          hint: a.fb_ad_id,
          group: "Объявления",
          icon: <Table size={15} />,
          run: () => {
            navigate({ to: "/ads/$fbAdId", params: { fbAdId: a.fb_ad_id } });
            setOpen(false);
          },
        });
      }
    }

    return list;
  }, [q, offersQ.data, adsQ.data, navigate, setOpen]);

  // Сброс выделения при смене запроса/открытии.
  useEffect(() => {
    setActive(0);
  }, [q, open]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, Math.max(items.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      items[active]?.run();
    }
  }

  // Группировка для рендера + сохранение плоского индекса для active.
  let flatIndex = -1;
  const groups = items.reduce<Record<string, CommandItem[]>>((acc, it) => {
    (acc[it.group] ??= []).push(it);
    return acc;
  }, {});

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-[1px] z-[60]" />
        <Dialog.Content
          onKeyDown={onKeyDown}
          aria-describedby={undefined}
          className="fixed left-1/2 top-[14%] -translate-x-1/2 w-[560px] max-w-[92vw] overflow-hidden rounded-[var(--radius-3)] bg-bg-1 border border-[var(--color-hairline-strong)] z-[60] focus:outline-none"
        >
          <Dialog.Title className="sr-only">Командная палитра</Dialog.Title>

          <div className="flex items-center gap-2.5 px-4 h-12 border-b border-[var(--color-hairline)]">
            <Search size={16} className="text-bg-9 shrink-0" aria-hidden="true" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Поиск объявлений, офферов, разделов…"
              aria-label="Поиск"
              className="flex-1 bg-transparent outline-none text-bg-11 text-[14px] font-body placeholder:text-bg-8"
            />
            <kbd className="font-display text-[12px] bg-bg-3 rounded-[var(--radius-1)] border border-[var(--color-hairline-strong)] px-[5px] py-px text-bg-9">
              ESC
            </kbd>
          </div>

          <div className="max-h-[360px] overflow-y-auto px-1.5 py-2">
            {items.length === 0 ? (
              <div className="px-4 py-10 text-center text-bg-9 text-[13px]">
                {q ? "Ничего не найдено" : "Начните вводить запрос"}
              </div>
            ) : (
              Object.entries(groups).map(([group, groupItems]) => (
                <div key={group} className="mb-1">
                  <div className="px-4 py-1.5 font-display text-[12px] uppercase tracking-[0.12em] text-bg-8">
                    {group}
                  </div>
                  {groupItems.map((it) => {
                    flatIndex += 1;
                    const idx = flatIndex;
                    const isActive = idx === active;
                    return (
                      <button
                        key={it.id}
                        type="button"
                        onMouseMove={() => setActive(idx)}
                        onClick={() => it.run()}
                        className={cn(
                          "flex min-h-11 w-full items-center gap-3 px-4 text-left text-[13.5px] transition-colors",
                          "rounded-[var(--radius-2)]",
                          isActive ? "bg-bg-3 text-accent" : "text-bg-11 hover:bg-bg-2",
                        )}
                      >
                        <span className={cn("shrink-0", isActive ? "text-accent" : "text-bg-9")}>
                          {it.icon}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{it.label}</span>
                        {it.hint && (
                          <span className="font-display text-[12px] text-bg-8 truncate max-w-[180px]">
                            {it.hint}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
