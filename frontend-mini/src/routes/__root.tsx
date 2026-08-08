/**
 * Корневой layout TMA Mini App.
 * AuthGuard → TelegramBackButton → контент (Outlet) → TabBar (нижний).
 * Root owns all four content safe-area insets; screens add only their normal spacing.
 */
import { createRootRoute, Outlet, useNavigate } from "@tanstack/react-router";
import {
  useCallback,
  useLayoutEffect,
  useRef,
  type PropsWithChildren,
} from "react";
import { AuthGuard } from "@/components/layout/AuthGuard";
import { TabBar } from "@/components/layout/TabBar";
import { TelegramBackButton } from "@/components/layout/TelegramBackButton";
import { useStoredToken } from "@/lib/auth";
import {
  beginResolvedNavigation,
  failResolvedNavigation,
  storeResolvedNavigation,
} from "@/lib/transientNavigation";
import {
  OperatorRealtimeStatusProvider,
  useOperatorRealtime,
  useOperatorRealtimeStatus,
} from "@fb/operator-api";
import {
  fetchOperatorActionProjectionsForRealtime,
  fetchOperatorSnapshotForRealtime,
  refreshTmaSession,
  useResolveTmaNavigation,
} from "@/lib/operatorApi";
import { getTgStartParam, tgAlert } from "@/lib/tg";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <AuthGuard>
      <OperatorRealtimeBridge>
        <OpaqueNavigationResolver />
        {/* Управляет нативной TG-кнопкой "Назад", DOM не рендерит */}
        <TelegramBackButton />

        {/* Основной контент — отступ снизу под TabBar (56px + safe-area) */}
        <main
          className="mx-auto flex min-h-[var(--tg-viewport-stable-height,100dvh)] max-w-[560px] flex-col"
          style={{
            paddingBottom:
              "calc(56px + var(--tg-content-safe-bottom, env(safe-area-inset-bottom, 0px)))",
            paddingTop:
              "var(--tg-content-safe-top, env(safe-area-inset-top, 0px))",
            paddingLeft:
              "var(--tg-content-safe-left, env(safe-area-inset-left, 0px))",
            paddingRight:
              "var(--tg-content-safe-right, env(safe-area-inset-right, 0px))",
          }}
        >
          <MiniRealtimeNotice />
          <Outlet />
        </main>

        {/* Нижний tab-bar — fixed, скрывается на detail-страницах */}
        <TabBar />
      </OperatorRealtimeBridge>
    </AuthGuard>
  );
}

export function OperatorRealtimeBridge({ children }: PropsWithChildren) {
  const token = useStoredToken();
  const refreshExpiredSession = useCallback(async () => {
    await refreshTmaSession(token);
  }, [token]);
  const status = useOperatorRealtime({
    enabled: Boolean(token),
    fetchActionProjections: fetchOperatorActionProjectionsForRealtime,
    fetchSnapshot: fetchOperatorSnapshotForRealtime,
    onAuthFailure: refreshExpiredSession,
    protocols: token ? ["fb-operator-v1", `tma.${token}`] : ["fb-operator-v1"],
  });
  return (
    <OperatorRealtimeStatusProvider status={status}>
      {children}
    </OperatorRealtimeStatusProvider>
  );
}

function MiniRealtimeNotice() {
  const status = useOperatorRealtimeStatus();
  if (status === "connected") return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-state="stale"
      className="mx-4 mt-4 rounded-[var(--radius-2)] border border-warning/40 bg-warning-bg px-4 py-3 text-[14px] leading-5 text-bg-11"
    >
      Live-связь восстанавливается. Данные устарели; денежные действия
      заблокированы до сверки снимка.
    </div>
  );
}

export function OpaqueNavigationResolver() {
  const navigate = useNavigate();
  const resolver = useResolveTmaNavigation();
  const started = useRef(false);

  useLayoutEffect(() => {
    if (started.current) return;
    const url = new URL(window.location.href);
    const queryToken = url.searchParams.get("nav");
    const token = queryToken ?? getTgStartParam();
    if (token === null) return;
    started.current = true;
    beginResolvedNavigation();

    // Remove the capability from the browser address and history before network I/O.
    url.searchParams.delete("nav");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
    void navigate({ to: "/open", replace: true });

    if (!/^[A-Za-z0-9_-]{22}$/.test(token)) {
      failResolvedNavigation();
      void tgAlert("Ссылка недействительна, истекла или уже использована.");
      return;
    }

    void resolver
      .mutateAsync({ body: { token } })
      .then((target) => {
        storeResolvedNavigation(target);
      })
      .catch(() => {
        failResolvedNavigation();
        void tgAlert("Ссылка недействительна, истекла или уже использована.");
      });
  }, [navigate, resolver]);

  return null;
}
