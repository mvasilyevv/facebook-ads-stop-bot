import js from "@eslint/js";
import reactPlugin from "eslint-plugin-react";
import reactHooksPlugin from "eslint-plugin-react-hooks";
import prettierConfig from "eslint-config-prettier";

export default [
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
    },
    languageOptions: {
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        fetch: "readonly",
        navigator: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        AbortController: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        performance: "readonly",
        confirm: "readonly",
        alert: "readonly",
        prompt: "readonly",
        MutationObserver: "readonly",
        ResizeObserver: "readonly",
        IntersectionObserver: "readonly",
        CustomEvent: "readonly",
        Event: "readonly",
        EventTarget: "readonly",
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: 2022,
        sourceType: "module",
      },
    },
    settings: {
      react: { version: "detect" },
    },
    rules: {
      ...reactPlugin.configs.recommended.rules,
      ...reactHooksPlugin.configs.recommended.rules,
      "react/react-in-jsx-scope": "off", // React 17+ не требует импорта
      "react/prop-types": "off", // JSX без TypeScript — prop-types не используем
      // Правила React Compiler (интегрированы в v7) — предупреждения для существующего кода
      "react/no-direct-mutation-state": "warn",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-console": "warn",
      "react-hooks/exhaustive-deps": "warn",
      // Правила React Compiler/19 — существующий код использует эти паттерны, понижаем до warn
      "react/jsx-no-constructed-context-values": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn", // Date.now() и другие "нечистые" вызовы — допустимо
      "no-empty": ["error", { "allowEmptyCatch": true }],
    },
  },
  prettierConfig,
  {
    ignores: ["dist/**", "node_modules/**"],
  },
];
