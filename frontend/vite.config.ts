/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// Operator web Vite config.
// - Dev port 5174.
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
      "@fb/operator-api": path.resolve(__dirname, "../packages/operator-api/src"),
      "@fb/operator-ui": path.resolve(__dirname, "../packages/operator-ui/src"),
      "@fb/shared": path.resolve(__dirname, "../packages/shared/src"),
    },
    // Storybook browser tests execute linked workspace source together with
    // renderer code. Keep one React dispatcher even after a cold optimizer run.
    dedupe: ["react", "react-dom"],
  },
  // Workspace-пакет @fb/shared потребляется как исходники (.ts) — не пре-бандлить,
  // чтобы HMR работал через границу пакета.
  optimizeDeps: {
    exclude: ["@fb/shared", "@fb/operator-api", "@fb/operator-ui"],
    // Linked workspace source hides these imports from Vite's initial crawl.
    // Discovering them after React mounts forces a dependency reload and can
    // temporarily mix two optimizer generations (invalid hook dispatcher).
    include: [
      "@radix-ui/react-toast",
      "@fb/operator-api > openapi-fetch",
      "@fb/operator-api > openapi-react-query",
      "zustand",
    ],
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
  // `vite preview` не наследует server.proxy, поэтому локальный preview использует
  // тот же API/WS target, что и dev server.
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
        },
      },
    },
  },
});
