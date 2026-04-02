import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        miniapp: resolve(__dirname, 'miniapp.html'),
      },
    },
  },
});
