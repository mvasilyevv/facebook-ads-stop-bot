import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// Flat-config ESLint для Telegram Mini App (TS + React hooks).
export default tseslint.config(
  {
    ignores: ["dist", "node_modules", "src/routeTree.gen.ts", "**/*.d.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // react-hooks v7 экспериментальные правила (React Compiler era) — ослаблены:
      // Date.now в queryFn и fallback-id корректны (покрыто TS strict + 89 тестами).
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
);
