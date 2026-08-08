import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { gzipSync } from "node:zlib";

const options = parseOptions(process.argv.slice(2));
const assetsDirectory = path.resolve(options.dist, "assets");
const assetNames = fs.readdirSync(assetsDirectory);
const startChunks = findStartChunks(
  assetNames,
  assetsDirectory,
  options.feature,
);

if (startChunks.length < 3) {
  throw new Error(
    `Could not resolve the entry, index route and ${options.feature} chunks in ${assetsDirectory}`,
  );
}

const initialChunks = collectStaticImports(startChunks, assetsDirectory);
const initialJsBytes = [...initialChunks].reduce(
  (total, filename) => total + gzipSize(path.join(assetsDirectory, filename)),
  0,
);
const initialJsHeadroom = options.jsBudget - initialJsBytes;
const fontBytes = assetNames
  .filter((filename) => filename.endsWith(".woff2"))
  .reduce(
    (total, filename) =>
      total + fs.statSync(path.join(assetsDirectory, filename)).size,
    0,
  );

console.log(
  `Frontend budgets: initial JS ${formatBytes(initialJsBytes)} / ${formatBytes(options.jsBudget)} ` +
    `(headroom ${formatBytes(initialJsHeadroom)}; required ${formatBytes(options.jsHeadroom)}); ` +
    `WOFF2 fonts ${formatBytes(fontBytes)} / ${formatBytes(options.fontBudget)}.`,
);

if (initialJsBytes > options.jsBudget) {
  throw new Error(
    `Initial route exceeds its gzip budget by ${formatBytes(initialJsBytes - options.jsBudget)}.`,
  );
}
if (initialJsHeadroom < options.jsHeadroom) {
  throw new Error(
    `Initial route headroom is ${formatBytes(initialJsHeadroom)}; ` +
      `${formatBytes(options.jsHeadroom)} is required.`,
  );
}
if (fontBytes > options.fontBudget) {
  throw new Error(
    `Fonts exceed their budget by ${formatBytes(fontBytes - options.fontBudget)}.`,
  );
}

function findStartChunks(names, directory, feature) {
  const startChunks = new Set();
  for (const filename of names.filter((name) => name.endsWith(".js.map"))) {
    const map = JSON.parse(
      fs.readFileSync(path.join(directory, filename), "utf8"),
    );
    if (
      map.sources.some(
        (source) =>
          source.endsWith("/src/main.tsx") ||
          source.endsWith("/src/routes/index.tsx?tsr-split=component") ||
          source.endsWith(`/src/features/operator/${feature}`),
      )
    ) {
      startChunks.add(filename.slice(0, -4));
    }
  }
  return [...startChunks];
}

function collectStaticImports(startChunks, directory) {
  const seen = new Set();
  const visit = (filename) => {
    if (seen.has(filename) || !fs.existsSync(path.join(directory, filename)))
      return;
    seen.add(filename);
    const source = fs.readFileSync(path.join(directory, filename), "utf8");
    for (const match of source.matchAll(
      /(?:from|import)\s*["']\.\/([^"']+\.js)["']/g,
    )) {
      visit(match[1]);
    }
  };
  startChunks.forEach(visit);
  return seen;
}

function gzipSize(filename) {
  return gzipSync(fs.readFileSync(filename)).byteLength;
}

function formatBytes(bytes) {
  return `${bytes.toLocaleString("en-US")} B`;
}

function parseOptions(args) {
  const value = (name) => {
    const index = args.indexOf(name);
    if (index < 0 || !args[index + 1]) throw new Error(`Missing ${name}`);
    return args[index + 1];
  };
  const optionalValue = (name) => {
    const index = args.indexOf(name);
    if (index < 0) return undefined;
    if (!args[index + 1]) throw new Error(`Missing ${name}`);
    return args[index + 1];
  };
  const jsHeadroom = Number(optionalValue("--js-headroom") ?? 0);
  if (!Number.isFinite(jsHeadroom) || jsHeadroom < 0) {
    throw new Error("--js-headroom must be a non-negative number");
  }
  return {
    dist: value("--dist"),
    feature: value("--feature"),
    jsBudget: Number(value("--js-budget")),
    jsHeadroom,
    fontBudget: Number(value("--font-budget")),
  };
}
