#!/usr/bin/env node
// -*- coding: utf-8 -*-
// Доставляет нативные биндинги rollup и esbuild для всех актуальных платформ.
//
// Зачем: vite использует rollup и esbuild с платформо-зависимыми бинарниками.
// npm ставит только биндинг для текущей платформы, и при смене Node-бинаря
// (например, Cursor использует arm64 Node, а run.sh — системный x64 Node)
// vite падает с MODULE_NOT_FOUND. Этот скрипт идемпотентно доустанавливает
// недостающие биндинги для darwin-arm64/x64 и linux-arm64/x64. См. также
// https://github.com/npm/cli/issues/4828.

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectDir = resolve(__dirname, '..');
const nodeModulesDir = resolve(projectDir, 'node_modules');

// Если node_modules ещё нет — это первичный install через npm install;
// postinstall сработает позже, при следующем install уже точно будет директория.
if (!existsSync(nodeModulesDir)) {
  process.exit(0);
}

function readInstalledVersion(packageName) {
  const pkgPath = resolve(nodeModulesDir, packageName, 'package.json');
  if (!existsSync(pkgPath)) return null;
  try {
    return JSON.parse(readFileSync(pkgPath, 'utf8')).version || null;
  } catch {
    return null;
  }
}

// Платформы, которые поддерживаем: macOS Intel/Apple Silicon и Linux x64/arm64.
const PLATFORMS = [
  { rollup: '@rollup/rollup-darwin-arm64', esbuild: '@esbuild/darwin-arm64' },
  { rollup: '@rollup/rollup-darwin-x64', esbuild: '@esbuild/darwin-x64' },
  { rollup: '@rollup/rollup-linux-x64-gnu', esbuild: '@esbuild/linux-x64' },
  { rollup: '@rollup/rollup-linux-arm64-gnu', esbuild: '@esbuild/linux-arm64' },
];

const rollupVersion = readInstalledVersion('rollup');
const esbuildVersion = readInstalledVersion('esbuild');

const targets = [];
for (const platform of PLATFORMS) {
  if (rollupVersion && !existsSync(resolve(nodeModulesDir, platform.rollup))) {
    targets.push(`${platform.rollup}@${rollupVersion}`);
  }
  if (esbuildVersion && !existsSync(resolve(nodeModulesDir, platform.esbuild))) {
    targets.push(`${platform.esbuild}@${esbuildVersion}`);
  }
}

if (targets.length === 0) {
  process.exit(0);
}

console.log(`[install-native-bindings] доустанавливаю: ${targets.join(', ')}`);

const result = spawnSync(
  'npm',
  [
    'install',
    '--no-save',
    '--ignore-scripts',
    '--force',
    '--silent',
    '--no-audit',
    '--no-fund',
    ...targets,
  ],
  { stdio: 'inherit', cwd: projectDir },
);

// Не валим build, если что-то не поставилось — лишь предупреждаем.
if (result.status !== 0) {
  console.warn(
    '[install-native-bindings] не удалось доустановить нативные биндинги; ' +
      'vite может не запуститься под другой архитектурой Node.',
  );
}
