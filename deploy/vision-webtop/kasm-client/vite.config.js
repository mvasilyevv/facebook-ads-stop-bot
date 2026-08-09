import { defineConfig } from "vite";

export default defineConfig({
  base: "",
  build: {
    sourcemap: false,
    minify: true,
    rollupOptions: {
      input: {
        main: "./index.html",
        screen: "./screen.html",
      },
      output: {
        entryFileNames: "[name].bundle.js",
      },
    },
  },
});
