import { Outlet } from "react-router-dom";
import { OperatorScopeBar } from "./OperatorScopeBar";
import { Sidebar } from "./Sidebar";
import { OperatorScopeProvider, useOperatorScope } from "../context/OperatorScopeContext";

function LayoutShell() {
  const scope = useOperatorScope();

  return (
    <div className="app-layout">
      <Sidebar />
      <div className="app-layout__main">
        <div className="background-grid" />
        <main className="page-content">
          {scope ? (
            <OperatorScopeBar
              profiles={scope.profiles}
              launches={scope.launches}
              selectedProfileId={scope.selectedProfileId}
              selectedLaunchId={scope.selectedLaunchId}
              loading={scope.loading}
              error={scope.error}
              onSelectProfile={scope.selectProfile}
              onSelectLaunch={scope.selectLaunch}
              onRenameLaunch={async (launchId, name) => {
                await scope.renameLaunch(launchId, name);
              }}
            />
          ) : null}
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function Layout() {
  return (
    <OperatorScopeProvider>
      <LayoutShell />
    </OperatorScopeProvider>
  );
}
