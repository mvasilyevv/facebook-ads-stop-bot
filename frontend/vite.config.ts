import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

function apiProxy(target: string) {
  return {
    target,
    changeOrigin: true,
    bypass(req: { headers: { accept?: string } }) {
      if (req.headers.accept?.includes("text/html")) {
        return "/index.html";
      }
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_API_BASE_URL || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/health": apiProxy(backend),
        "/ads": apiProxy(backend),
        "/decisions": apiProxy(backend),
        "/rules": apiProxy(backend),
        "/offers": apiProxy(backend),
        "/sessions": apiProxy(backend),
        "/scan-runs": apiProxy(backend),
        "/control-flags": apiProxy(backend),
        "/settings": apiProxy(backend),
        "/offer-bindings": apiProxy(backend),
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: "./tests/setup.ts",
      include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
      css: true,
    },
  };
});
