import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Конфигурация Vite для Telegram Mini App
export default defineConfig({
  plugins: [react()],
  base: "/tma/",
  resolve: {
    alias: {
      // @shared указывает на общие утилиты и хуки из основного фронтенда
      "@shared": path.resolve(__dirname, "../frontend/src/shared"),
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
    },
  },
});
