import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(process.cwd(), "src");
const routesDir = join(srcDir, "routes");

describe("TMA route architecture", () => {
  it("physically excludes legacy routes and keeps the typed campaign creator", () => {
    for (const routeFile of [
      "history/index.tsx",
      "stats/index.tsx",
      "scripts/index.tsx",
      "campaigns/launch/index.tsx",
    ]) {
      expect(existsSync(join(routesDir, routeFile)), routeFile).toBe(false);
    }

    const routeTree = readFileSync(join(srcDir, "routeTree.gen.ts"), "utf8");
    for (const forbiddenRoute of ["/history/", "/stats/", "/scripts/"]) {
      expect(routeTree, forbiddenRoute).not.toContain(forbiddenRoute);
    }
    expect(routeTree).toContain("/analytics/");
    expect(routeTree).toContain("/campaigns/create/");
  });

  it("uses the typed full campaign flow without retired script or clone paths", () => {
    const operatorApi = readFileSync(
      join(srcDir, "lib/operatorApi.ts"),
      "utf8",
    );
    const legacyApi = readFileSync(join(srcDir, "lib/api.ts"), "utf8");
    const campaignsApi = readFileSync(join(srcDir, "lib/campaigns.ts"), "utf8");
    const productionClient = `${operatorApi}\n${legacyApi}\n${campaignsApi}`;

    for (const forbiddenCapability of [
      "/api/tools/campaigns/runs/{run_id}/clone",
      "/tools/campaign-create/folders",
      "/tools/campaign-create/plan",
      "useCloneRun",
      "useScriptPlan",
    ]) {
      expect(productionClient, forbiddenCapability).not.toContain(
        forbiddenCapability,
      );
    }
    expect(campaignsApi).toContain('"/api/tools/campaigns/launch"');
    expect(campaignsApi).toContain('"/api/tools/campaigns/draft"');
  });

  it("gives /campaigns the same creation journal meaning as web", () => {
    const campaignsRoute = readFileSync(
      join(routesDir, "campaigns/index.tsx"),
      "utf8",
    );

    expect(campaignsRoute).toContain("RunsHistory");
    expect(campaignsRoute).toContain('to="/campaigns/create"');
    expect(campaignsRoute).not.toContain("DESKTOP-FIRST");
  });

  it("does not retain unused compatibility barrels and metric cards", () => {
    for (const retiredFile of [
      "components/domain/MetricsGrid.tsx",
      "components/layout/index.ts",
    ]) {
      expect(existsSync(join(srcDir, retiredFile)), retiredFile).toBe(false);
    }
  });
});
