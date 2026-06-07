#!/usr/bin/env node
// Пакетный рендер креативов через Remotion из JSON-файла пропсов.
// Использование:
//   node scripts/render-batch.mjs --input batch.json --out ./out [--bg clean.mp4] [--format 9x16]
// Каждый элемент input — объект пропсов (creativeSchema) + опц. поле format.
import {execFileSync} from 'node:child_process';
import {copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {basename, dirname, isAbsolute, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const FORMAT_COMP = {'9x16': 'Creative9x16', '1x1': 'Creative1x1', '16x9': 'Creative16x9'};

function parseArgs(argv) {
  const args = {out: join(ROOT, 'out'), format: '9x16'};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--input') args.input = argv[++i];
    else if (a === '--out') args.out = argv[++i];
    else if (a === '--bg') args.bg = argv[++i];
    else if (a === '--format') args.format = argv[++i];
  }
  if (!args.input) {
    console.error('render-batch: требуется --input <props.json>');
    process.exit(2);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const remotionBin = join(ROOT, 'node_modules', '.bin', 'remotion');
  if (!existsSync(remotionBin)) {
    console.error('Remotion не установлен — выполните: cd remotion && npm ci');
    process.exit(3);
  }

  const items = JSON.parse(readFileSync(args.input, 'utf-8'));
  if (!Array.isArray(items) || items.length === 0) {
    console.error('render-batch: input пуст или не массив');
    process.exit(2);
  }

  // Общий фон-видео кладём в public/ — Remotion staticFile грузит оттуда.
  let bgName;
  if (args.bg) {
    const src = isAbsolute(args.bg) ? args.bg : resolve(process.cwd(), args.bg);
    if (!existsSync(src)) {
      console.error(`--bg не найден: ${src}`);
      process.exit(2);
    }
    mkdirSync(join(ROOT, 'public'), {recursive: true});
    bgName = basename(src);
    copyFileSync(src, join(ROOT, 'public', bgName));
  }

  mkdirSync(args.out, {recursive: true});
  const propsFile = join(ROOT, '.render-props.json');
  let ok = 0;

  for (const item of items) {
    const format = item.format || args.format;
    const comp = FORMAT_COMP[format];
    if (!comp) {
      console.error(`пропускаю ${item.code}: неизвестный формат ${format}`);
      continue;
    }
    const props = {...item};
    delete props.format;
    if (bgName) props.bg = {type: 'video', src: bgName};
    writeFileSync(propsFile, JSON.stringify(props), 'utf-8');

    const outFile = join(args.out, `${item.code}_${format}.mp4`);
    console.log(`▸ рендер ${item.code} [${format}] → ${outFile}`);
    // Запускаем Remotion тем же node, что и этот скрипт (а не node из PATH) —
    // гарантия версии Node, под которую ставился Remotion.
    execFileSync(
      process.execPath,
      [remotionBin, 'render', 'src/index.ts', comp, outFile, `--props=${propsFile}`, '--log=error'],
      {cwd: ROOT, stdio: 'inherit'}
    );
    ok++;
  }
  console.log(`\n✅ отрендерено: ${ok}/${items.length} → ${args.out}`);
}

main();
