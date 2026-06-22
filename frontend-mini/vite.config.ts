/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// Telegram Mini App — тот же дизайн-канон, что веб; адаптация под мобайл.
// - base /tma/ (бэкенд монтирует Mini App на этот префикс).
// - Порт 5175 (web — 5174).
// - Tailwind 4 + TanStack Router, как в основном фронте.
// - @fb/shared потребляется как исходники (HMR через границу пакета).
export default defineConfig({
  plugins: [
    TanStackRouterVite({
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/routeTree.gen.ts",
      // Code-splitting только для прод-сборки (см. frontend/vite.config.ts).
      autoCodeSplitting: !process.env.VITEST,
      // Компоненты шагов визарда и вспомогательные файлы — не роуты
      routeFileIgnorePattern: "^(Step|RunsHistory)",
    }),
    react(),
    tailwindcss(),
  ],
  base: "/tma/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@fb/shared": path.resolve(__dirname, "../packages/shared/src"),
    },
  },
  optimizeDeps: {
    exclude: ["@fb/shared"],
  },
  server: {
    port: 5175,
    host: true,
    allowedHosts: true,
    proxy: {
      "/api": { target: "http://localhost:8100", changeOrigin: true },
      "/ws": { target: "ws://localhost:8100", ws: true, changeOrigin: true },
    },
  },
  preview: {
    port: 5175,
    proxy: {
      "/api": { target: "http://localhost:8100", changeOrigin: true },
      "/ws": { target: "ws://localhost:8100", ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
  },
});
