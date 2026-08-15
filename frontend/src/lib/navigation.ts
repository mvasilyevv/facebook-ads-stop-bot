/**
 * Разделы операторского интерфейса — единственный источник.
 *
 * Меню слева и надзаголовок экрана берут номер и название отсюда, поэтому
 * разойтись между собой они не могут. Раньше надзаголовок печатали на каждом
 * экране руками, и он разошёлся: Аналитика значилась третьей в меню и пятой
 * над заголовком, а номер `05` носили пять экранов сразу.
 *
 * Словарь русский. Внутренние слова — OPERATE, PERFORMANCE, CATALOG, REMOTE —
 * оператору не говорят ничего и здесь запрещены.
 */

import {
  LayoutDashboard,
  Activity,
  Layers,
  Radar,
  Tag,
  BarChart3,
  Database,
  Settings,
  MonitorUp,
  Rocket,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Ключ для подстановки count-badge. */
  badgeKey?: "actions";
  /** Вложенные пункты (отрисовываются с отступом под родителем). */
  children?: NavItem[];
}

export interface NavGroup {
  /** Номер раздела: тот же, что оператор видит в меню. */
  num: string;
  /** Название раздела заглавными — как в меню. */
  name: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    num: "01",
    name: "ОПЕРАЦИИ",
    items: [
      { to: "/", label: "Сейчас", icon: LayoutDashboard },
      { to: "/actions", label: "Действия", icon: Activity, badgeKey: "actions" },
    ],
  },
  {
    num: "02",
    name: "РЕКЛАМА",
    items: [
      { to: "/ads", label: "Объявления", icon: Layers },
      {
        to: "/campaigns",
        label: "Кампании",
        icon: Radar,
        children: [{ to: "/campaigns/create", label: "Создание", icon: Rocket }],
      },
      { to: "/offers", label: "Офферы", icon: Tag },
    ],
  },
  {
    num: "03",
    name: "АНАЛИТИКА",
    items: [{ to: "/analytics", label: "Аналитика", icon: BarChart3 }],
  },
  {
    num: "04",
    name: "СИСТЕМА",
    items: [
      { to: "/system/sources", label: "Источники и воркеры", icon: Database },
      { to: "/remote-desktop", label: "Рабочий стол", icon: MonitorUp },
      { to: "/settings", label: "Настройки", icon: Settings },
    ],
  },
];

/**
 * Экраны, до которых добираются не через меню, а по ссылке из данных.
 * Пункта в меню у них нет, но раздел есть — иначе надзаголовок пришлось бы
 * снова печатать руками.
 */
const OFF_MENU_ROUTES: ReadonlyArray<{
  prefix: string;
  groupNum: string;
  /** Пункт, под которым лежит сам экран. */
  parent?: string;
  /** Как экран называется в крошке своих карточек. */
  selfCrumb: string;
}> = [
  // Инциденты и кабинеты — самостоятельные экраны раздела: пункта в меню у них
  // нет, но и родителя над ними тоже нет. Крошку получают только карточки.
  { prefix: "/incidents", groupNum: "01", selfCrumb: "ИНЦИДЕНТЫ" },
  { prefix: "/cabinets", groupNum: "01", selfCrumb: "КАБИНЕТЫ" },
  // Пресеты своего пункта не имеют и живут под Кампаниями.
  { prefix: "/campaigns/presets", groupNum: "02", parent: "КАМПАНИИ", selfCrumb: "ШАБЛОНЫ" },
];

export interface SectionPlacement {
  /** Номер раздела для надзаголовка. */
  num: string;
  /** Название раздела для надзаголовка. */
  name: string;
  /**
   * Родительский пункт меню, если экран лежит под ним. Заголовок вложенного
   * экрана называет запись, а не раздел, поэтому путь до неё нужно показать.
   */
  crumb?: string;
}

function normalize(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith("/")) return pathname.slice(0, -1);
  return pathname;
}

function isUnder(pathname: string, base: string): boolean {
  if (base === "/") return pathname === "/";
  return pathname === base || pathname.startsWith(`${base}/`);
}

/**
 * Раздел, которому принадлежит путь. Возвращает `null` для маршрутов вне
 * продукта (например, 404) — надзаголовок в этом случае не рисуется.
 */
export function sectionForPath(pathname: string): SectionPlacement | null {
  const path = normalize(pathname);

  const offMenu = OFF_MENU_ROUTES.filter((route) => isUnder(path, route.prefix)).sort(
    (a, b) => b.prefix.length - a.prefix.length,
  )[0];
  if (offMenu) {
    const group = NAV_GROUPS.find((candidate) => candidate.num === offMenu.groupNum);
    if (group) {
      return {
        num: group.num,
        name: group.name,
        crumb: path === offMenu.prefix ? offMenu.parent : offMenu.selfCrumb,
      };
    }
  }

  let best: SectionPlacement | null = null;
  let bestLength = -1;

  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      const candidates = [item, ...(item.children ?? [])];
      for (const candidate of candidates) {
        if (!isUnder(path, candidate.to)) continue;
        if (candidate.to.length <= bestLength) continue;
        bestLength = candidate.to.length;
        best = {
          num: group.num,
          name: group.name,
          // Точное совпадение — заголовок экрана и есть пункт меню, крошка
          // повторила бы его. Вложенный экран называет запись, крошка нужна.
          crumb: path === candidate.to ? undefined : candidate.label.toUpperCase(),
        };
      }
    }
  }

  return best;
}
