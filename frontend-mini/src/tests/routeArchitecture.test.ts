import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(process.cwd(), "src");
const routesDir = join(srcDir, "routes");

describe("TMA route architecture", () => {
  it("physically excludes legacy history, stats and campaign-script routes", () => {
    for (const routeFile of [
      "history/index.tsx",
      "stats/index.tsx",
      "scripts/index.tsx",
      "campaigns/create/index.tsx",
      "campaigns/launch/index.tsx",
    ]) {
      expect(existsSync(join(routesDir, routeFile)), routeFile).toBe(false);
    }

    const routeTree = readFileSync(join(srcDir, "routeTree.gen.ts"), "utf8");
    for (const forbiddenRoute of [
      "/history/",
      "/stats/",
      "/scripts/",
      "/campaigns/create",
    ]) {
      expect(routeTree, forbiddenRoute).not.toContain(forbiddenRoute);
    }
    expect(routeTree).toContain("/analytics/");
  });

  it("keeps the mobile campaign surface progress-only", () => {
    const operatorApi = readFileSync(
      join(srcDir, "lib/operatorApi.ts"),
      "utf8",
    );
    const legacyApi = readFileSync(join(srcDir, "lib/api.ts"), "utf8");
    const productionClient = `${operatorApi}\n${legacyApi}`;

    for (const forbiddenCapability of [
      "/api/tools/campaigns/launch",
      "/api/tools/campaigns/runs/{run_id}/clone",
      "/tools/campaign-create/folders",
      "/tools/campaign-create/plan",
      "useLaunchCampaign",
      "useCloneRun",
      "useScriptPlan",
    ]) {
      expect(productionClient, forbiddenCapability).not.toContain(
        forbiddenCapability,
      );
    }
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
