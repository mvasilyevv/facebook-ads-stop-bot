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
];

function productionFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      if (["dist", "node_modules", "test", "tests"].includes(entry.name))
        return [];
      return productionFiles(path);
    }
    if (!entry.isFile()) return [];
    if (/\.(?:test|spec|stories)\.[^.]+$/.test(entry.name)) return [];
    return entry.name.endsWith(".tsx") || entry.name.endsWith(".css")
      ? [path]
      : [];
  });
}

function microtextFindings(path: string): string[] {
  const source = readFileSync(path, "utf8");
  const findings: string[] = [];
  const checks = [
    {
      label: "Tailwind text",
      pattern: /text-\[(\d+(?:\.\d+)?)px\]/g,
      pixelsPerUnit: 1,
    },
    {
      label: "Tailwind text",
      pattern: /text-\[(\d+(?:\.\d+)?)rem\]/g,
      pixelsPerUnit: 16,
    },
    {
      label: "inline fontSize",
      pattern: /fontSize\s*:\s*(?:(\d+(?:\.\d+)?)|["'](\d+(?:\.\d+)?)px["'])/g,
      pixelsPerUnit: 1,
    },
    {
      label: "inline fontSize",
      pattern: /fontSize\s*:\s*["'](\d+(?:\.\d+)?)rem["']/g,
      pixelsPerUnit: 16,
    },
    {
      label: "CSS font-size",
      pattern: /font-size\s*:\s*(\d+(?:\.\d+)?)px/g,
      pixelsPerUnit: 1,
    },
    {
      label: "CSS font-size",
      pattern: /font-size\s*:\s*(\d+(?:\.\d+)?)rem/g,
      pixelsPerUnit: 16,
    },
  ];

  for (const { label, pattern, pixelsPerUnit } of checks) {
    for (const match of source.matchAll(pattern)) {
      const size = Number(match[1] ?? match[2]) * pixelsPerUnit;
      if (size >= 12) continue;
      const line = source.slice(0, match.index).split("\n").length;
      findings.push(`${relative(repoRoot, path)}:${line} ${label} ${size}px`);
    }
  }
  return findings;
}

describe("production typography policy", () => {
  it("keeps all production service text at 12px or larger", () => {
    const findings = productionRoots
      .flatMap(productionFiles)
      .flatMap(microtextFindings);

    expect(findings, findings.join("\n")).toEqual([]);
  });

  it("keeps production foreground text, placeholders and icons off the bg7 border token", () => {
    const findings = productionRoots
      .flatMap(productionFiles)
      .flatMap((path) => {
        const source = readFileSync(path, "utf8");
        // bg7 remains legal for structural borders and decorative chart strokes.
        // Foreground text/icons/placeholders must use the WCAG-AA bg8 token or stronger.
        const forbidden = [
          /(?:placeholder:)?text-(?:bg-7|\[var\(--color-bg-7\)\])/g,
          /color\s*:\s*["']var\(--color-bg-7\)["']/g,
          /\bfill-bg-7\b/g,
          /#4a4a52/gi,
        ];
        return forbidden.flatMap((pattern) =>
          [...source.matchAll(pattern)].map((match) => {
            const line = source.slice(0, match.index).split("\n").length;
            return `${relative(repoRoot, path)}:${line} ${match[0]}`;
          }),
        );
      });

    expect(findings, findings.join("\n")).toEqual([]);
  });
});
