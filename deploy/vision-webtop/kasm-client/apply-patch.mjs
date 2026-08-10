import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.argv[2] ?? ".");

function replaceOnce(source, marker, replacement, file) {
  const first = source.indexOf(marker);
  if (first < 0 || source.indexOf(marker, first + marker.length) >= 0) {
    throw new Error(`Pinned Kasm source marker mismatch: ${file}`);
  }
  return (
    source.slice(0, first) + replacement + source.slice(first + marker.length)
  );
}

const indexPath = resolve(root, "index.html");
let index = readFileSync(indexPath, "utf8");
index = replaceOnce(
  index,
  '<html lang="en"',
  '<html lang="ru"',
  "index.html:lang",
);
index = replaceOnce(
  index,
  "<title>KasmVNC</title>",
  "<title>FB Agent · Рабочий стол</title>",
  "index.html:title",
);
index = replaceOnce(
  index,
  '    <link rel="stylesheet" href="app/styles/base.css">',
  '    <link rel="stylesheet" href="app/styles/base.css">\n    <link rel="stylesheet" href="app/styles/fb-agent-client.css">',
  "index.html:styles",
);
index = replaceOnce(
  index,
  '    <script type="module" crossorigin="use-credentials" src="app/ui.js"></script>',
  '    <script type="module" crossorigin="use-credentials" src="app/ui.js"></script>\n    <script type="module" crossorigin="use-credentials" src="app/fb-agent-client.js"></script>',
  "index.html:scripts",
);
writeFileSync(indexPath, index);

const rfbPath = resolve(root, "core/rfb.js");
let rfb = readFileSync(rfbPath, "utf8").replaceAll("\r\n", "\n");
const scaleViewport = `    get scaleViewport() { return this._scaleViewport; }
    set scaleViewport(scale) {
        if (this._scaleViewport !== scale) {
            this._scaleViewport = scale;
            this._pendingApplyResolutionChange = true;
        }
    }
`;
const localScale = `${scaleViewport}
    // FB Agent local-only zoom. It never requests a server-side resize.
    get localScale() { return this._display.scale; }
    set localScale(scale) {
        const value = Number(scale);
        if (!Number.isFinite(value) || value < 0.25 || value > 2) {
            throw new TypeError("Local scale must be between 0.25 and 2");
        }
        this._resizeSession = false;
        this._scaleViewport = false;
        this._clipViewport = true;
        this._display.clipViewport = true;
        this._display.scale = value;
        this._pendingApplyResolutionChange = true;
    }
`;
rfb = replaceOnce(rfb, scaleViewport, localScale, "core/rfb.js:local-scale");
writeFileSync(rfbPath, rfb);
