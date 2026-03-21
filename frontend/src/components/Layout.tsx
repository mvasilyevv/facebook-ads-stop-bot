import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="app-layout__main">
        <div className="background-grid" />
        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
