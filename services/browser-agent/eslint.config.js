// Minimal flat config for every browser-agent TypeScript source. Type-aware
// correctness remains enforced by `tsc`; this pass catches syntax/control-flow
// mistakes without depending on a retired feature-specific directory.
const tsParser = require('@typescript-eslint/parser');

module.exports = [
  {
    files: ['src/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
      },
    },
    rules: {
      'no-constant-condition': ['error', { checkLoops: false }],
      'no-debugger': 'error',
      'no-duplicate-imports': 'error',
      'no-unreachable': 'error',
    },
  },
];
