import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/", label: "Обзор", icon: "◉" },
  { to: "/ads", label: "Объявления", icon: "◧" },
  { to: "/settings", label: "Настройки", icon: "⚙" },
  { to: "/decisions", label: "Решения", icon: "◈" },
  { to: "/offers", label: "Офферы", icon: "❖" },
  { to: "/sessions", label: "Сессии", icon: "◎" },
  { to: "/scans", label: "Сканы", icon: "↻" },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__logo">FB</span>
        <span className="sidebar__title">Ad Manager</span>
      </div>
      <nav className="sidebar__nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `sidebar__link${isActive ? " sidebar__link--active" : ""}`
            }
          >
            <span className="sidebar__icon">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
