import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(process.cwd(), "src");

describe("web route architecture", () => {
  it("does not retain compatibility-only stats and history routes", () => {
    for (const routeFile of ["stats/index.tsx", "history/index.tsx"]) {
      expect(existsSync(join(srcDir, "routes", routeFile)), routeFile).toBe(false);
    }

    const routeTree = readFileSync(join(srcDir, "routeTree.gen.ts"), "utf8");
    expect(routeTree).not.toContain("/stats/");
    expect(routeTree).not.toContain("/history/");
    expect(routeTree).toContain("/analytics/");
  });

  it("keeps command search on the typed operator ads contract", () => {
    const palette = readFileSync(join(srcDir, "components/layout/CommandPalette.tsx"), "utf8");
    const packageJson = readFileSync(resolve(process.cwd(), "package.json"), "utf8");

    expect(palette).toContain("useOperatorAds");
    expect(palette).not.toContain("@/lib/api/ads");
    expect(palette).not.toContain("/dashboard/ads");
    expect(existsSync(join(srcDir, "lib/api/ads.ts"))).toBe(false);

    for (const retiredDependency of [
      "@radix-ui/react-popover",
      "@tanstack/react-query-devtools",
      "@tanstack/react-table",
      "@tanstack/react-virtual",
    ]) {
      expect(packageJson, retiredDependency).not.toContain(retiredDependency);
    }
  });

  it("physically excludes the retired dashboard realtime implementation", () => {
    for (const retiredFile of [
      "lib/websocket/useDashboardSocket.ts",
      "lib/websocket/useRealtimeInvalidation.ts",
      "lib/api/dashboard.ts",
      "components/dashboard/LiveTail.tsx",
      "components/dashboard/TaskQueues.tsx",
    ]) {
      expect(existsSync(join(srcDir, retiredFile)), retiredFile).toBe(false);
    }
  });

  it("gives /campaigns the creation journal meaning shared with TMA", () => {
    const campaignsRoute = readFileSync(join(srcDir, "routes/campaigns/index.tsx"), "utf8");

    expect(campaignsRoute).toContain("CampaignRunsHistory");
    expect(campaignsRoute).toContain('to="/campaigns/create"');
    expect(campaignsRoute).not.toContain("useObserverCampaigns");
    expect(campaignsRoute).not.toContain("CabinetAutostart");
  });

  it("returns the standalone offer-rules route to the offer catalog", () => {
    const offerRulesRoute = readFileSync(join(srcDir, "routes/offers/$id.tsx"), "utf8");

    expect(offerRulesRoute).toContain('navigate({ to: "/offers" })');
    expect(offerRulesRoute).not.toContain('navigate({ to: "/" })');
  });

  it("uses canonical operator events and observer exclusions only", () => {
    const operatorClient = readFileSync(join(srcDir, "lib/api/operator.ts"), "utf8");
    const settingsClient = readFileSync(join(srcDir, "lib/api/settings.ts"), "utf8");

    expect(operatorClient).toContain('"/api/operator/events"');
    expect(operatorClient).not.toContain('"/history/');
    expect(settingsClient).not.toContain("/auto-enable");
    expect(settingsClient).not.toContain("cabinet-autostart");
    expect(settingsClient).not.toContain("auto_enable_recommendations");
    expect(existsSync(join(srcDir, "lib/api/history.ts"))).toBe(false);
  });

  it("keeps Recharts outside the operator start route and enforces bundle headroom", () => {
    const dashboard = readFileSync(join(srcDir, "features/operator/OperatorDashboard.tsx"), "utf8");
    const chartPlot = readFileSync(
      join(srcDir, "features/operator/OperatorSpendChartPlot.tsx"),
      "utf8",
    );
    const packageJson = readFileSync(resolve(process.cwd(), "package.json"), "utf8");

    expect(dashboard).not.toContain("OperatorSpendChartPlot");
    expect(dashboard).not.toContain('from "recharts"');
    expect(chartPlot).toContain('from "recharts"');
    expect(packageJson).toContain("--js-budget 250000 --js-headroom 20000");
  });
});
