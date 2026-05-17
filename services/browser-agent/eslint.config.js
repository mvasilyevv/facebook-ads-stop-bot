// ESLint flat config. Запрещает прямой el.click() и el.value=... внутри
// creator/steps/, чтобы все взаимодействия шли через humanizer.ts.
const tsParser = require('@typescript-eslint/parser');

module.exports = [
  {
    files: ['src/creator/steps/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
      },
    },
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.property.name='click']",
          message: 'Используй humanClick() из humanizer.ts',
        },
        {
          selector: "AssignmentExpression[left.property.name='value']",
          message: 'Используй humanType() из humanizer.ts',
        },
      ],
    },
  },
];
