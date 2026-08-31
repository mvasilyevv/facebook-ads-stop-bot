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
      // Компоненты шагов визарда и вспомогательные файлы — не роуты.
      // ActionDetailView колокирован с $actionId, чтобы /open мог лениво
      // импортировать его напрямую (см. open.tsx) — иначе Rollup не может
      // вынести компонент в отдельный чанк, если тот же файл уже статически
      // импортирован routeTree.gen.ts как route. MiniAdDetail/MiniIncidentDetail
      // остались внутри $fbAdId.tsx/$incidentId.tsx и импортируются в /open
      // статически: они всё равно тянут OperatorAds, уже нужный дашборду, и
      // отдельный ленивый чанк для них обходится бюджету дороже, чем общий
      // поток (см. комментарий в open.tsx).
      routeFileIgnorePattern: "^(Step|RunsHistory|ActionDetailView)",
    }),
    react(),
    tailwindcss(),
  ],
  base: "/tma/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@fb/operator-api": path.resolve(__dirname, "../packages/operator-api/src"),
      "@fb/operator-ui": path.resolve(__dirname, "../packages/operator-ui/src"),
      "@fb/shared": path.resolve(__dirname, "../packages/shared/src"),
    },
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    exclude: ["@fb/shared", "@fb/operator-api", "@fb/operator-ui"],
    // @fb/operator-api is linked source, so Vite cannot discover these nested
    // imports during its initial crawl unless they are explicit.
    include: [
      "@fb/operator-api > openapi-fetch",
      "@fb/operator-api > openapi-react-query",
    ],
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
