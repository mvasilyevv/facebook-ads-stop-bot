import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const currentDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(currentDir, "../../../..");
const productionRoots = [
  join(repoRoot, "frontend/src"),
  join(repoRoot, "frontend-mini/src"),
  join(repoRoot, "packages/operator-ui/src"),
  join(repoRoot, "packages/features/src"),
];

function sourceFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      if (["dist", "node_modules", "test", "tests"].includes(entry.name)) return [];
      return sourceFiles(path);
    }
    if (!entry.isFile() || !/\.(?:ts|tsx)$/.test(entry.name)) return [];
    if (/\.(?:test|spec|stories)\.[^.]+$/.test(entry.name)) return [];
    return [path];
  });
}

describe("production interaction safety", () => {
  it("does not use blocking browser alert or confirm dialogs", () => {
    const forbidden = /\b(?:window\.)?(?:alert|confirm)\s*\(/g;
    const findings = productionRoots.flatMap(sourceFiles).flatMap((path) => {
      const source = readFileSync(path, "utf8");
      return [...source.matchAll(forbidden)].map((match) => {
        const line = source.slice(0, match.index).split("\n").length;
        return `${relative(repoRoot, path)}:${line} ${match[0]}`;
      });
    });

    expect(findings, findings.join("\n")).toEqual([]);
  });
});
