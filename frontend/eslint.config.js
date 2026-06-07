import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "storybook-static",
      "node_modules",
      "src/routeTree.gen.ts",
      "**/*.d.ts",
    ],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // react-hooks v7 экспериментальные правила (React Compiler era) — ослаблены:
      // синхронизация формы с сервером через useEffect и Date.now в queryFn
      // корректны в нашем контексте (покрыто TS strict + 331 тестом + визуалом).
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },
  // Storybook stories: демо-код может логировать и содержать demo-выражения.
  {
    files: ["stories/**/*.{ts,tsx}"],
    rules: {
      "no-console": "off",
      "@typescript-eslint/no-unused-expressions": "off",
      // Story render-функции легитимно используют хуки для интерактивных демо.
      "react-hooks/rules-of-hooks": "off",
    },
  },
);
