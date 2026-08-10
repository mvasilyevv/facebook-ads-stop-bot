import { defineConfig } from "@playwright/test";

const viewports = [360, 390, 430, 768, 1280, 1440, 1920];

function resolvePort(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1024 || value > 65_535) {
    throw new Error(`${name} must be an integer TCP port between 1024 and 65535`);
  }
  return value;
}

const webPort = resolvePort("PLAYWRIGHT_WEB_PORT", 4174);
const tmaPort = resolvePort("PLAYWRIGHT_TMA_PORT", 4275);
const webBaseURL = `http://127.0.0.1:${webPort}`;
const tmaBaseURL = `http://127.0.0.1:${tmaPort}/tma/`;

export default defineConfig({
  testDir: "./e2e",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{projectName}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: webBaseURL,
    colorScheme: "dark",
    locale: "ru-RU",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    ...viewports.map((width) => ({
      // Keep these stable Chromium project names for self-hosted pixel baselines.
      name: `${width}px`,
      grepInvert: /@tma/,
      use: {
        browserName: "chromium" as const,
        viewport: { width, height: width <= 430 ? 860 : 900 },
      },
    })),
    {
      name: "firefox-functional",
      grepInvert: /@(?:tma|visual)/,
      use: { browserName: "firefox", viewport: { width: 1280, height: 900 } },
    },
    {
      name: "webkit-functional",
      grepInvert: /@(?:tma|visual)/,
      use: { browserName: "webkit", viewport: { width: 1280, height: 900 } },
    },
    ...[360, 390, 430, 768].map((width) => ({
      name: `tma-${width}px`,
      grep: /@tma/,
      use: {
        baseURL: tmaBaseURL,
        browserName: "chromium" as const,
        viewport: { width, height: width <= 430 ? 844 : 900 },
      },
    })),
    {
      name: "tma-webkit-functional",
      grep: /@tma/,
      use: {
        baseURL: tmaBaseURL,
        browserName: "webkit",
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: [
    {
      command: `pnpm vite preview --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: webBaseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command:
        `pnpm --dir ../frontend-mini build && pnpm --dir ../frontend-mini exec vite preview ` +
        `--host 127.0.0.1 --port ${tmaPort} --strictPort`,
      url: tmaBaseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
