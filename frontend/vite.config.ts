/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// Vite config для нового фронта.
// - Порт 5174 (старый фронт 5173).
// - Proxy /api → http://localhost:8100 (FastAPI backend).
// - Tailwind 4 через @tailwindcss/vite — без отдельного PostCSS pipeline.
// - TanStack Router plugin — file-based routing с автогенерацией routeTree.
export default defineConfig({
  plugins: [
    TanStackRouterVite({
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/routeTree.gen.ts",
      // Code-splitting только для прод-сборки. В vitest он требует lazyRouteComponent,
      // который юнит-тесты не мокают → route-компоненты тестируются напрямую.
      autoCodeSplitting: !process.env.VITEST,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@fb/shared": path.resolve(__dirname, "../packages/shared/src"),
    },
  },
  // Workspace-пакет @fb/shared потребляется как исходники (.ts) — не пре-бандлить,
  // чтобы HMR работал через границу пакета.
  optimizeDeps: {
    exclude: ["@fb/shared"],
  },
  server: {
    port: 5174,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8100",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  // preview НЕ наследует server.proxy — дублируем для prod-режима (./run.sh без --dev,
  // фронт сервится через `vite preview`). Тот же таргет API/WS, что и в dev.
  preview: {
    port: 5174,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8100",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          router: ["@tanstack/react-router"],
          query: ["@tanstack/react-query"],
          charts: ["recharts"],
        },
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    css: false,
  },
});
