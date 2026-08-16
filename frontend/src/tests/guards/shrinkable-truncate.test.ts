import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Сжимаемость важнее вкуса: у flex- и grid-элемента min-width по умолчанию
 * auto, поэтому элемент не может стать уже содержимого. Пока на нём нет
 * min-w-0 (JS/TSX) или min-width: 0 (CSS), соседний truncate/ellipsis не
 * срабатывает — длинное имя кампании, кабинета или ключ брокера выталкивает
 * карточку за экран. Владелец находил это дважды на живом телефоне; тест
 * держит закрытой ту часть класса дефекта, которую вообще можно поймать
 * текстовым сканом файлов, а не рендером реального DOM.
 *
 * ЧТО ТЕСТ ЛОВИТ (два независимых канала, оба — буквальное совпадение
 * сигнатуры дефекта, а не любое использование truncate/ellipsis):
 *
 *  1. JS/TSX: className="..." , className={`...`} и строковые литералы
 *     внутри className={cn(...)} — включая вызовы cn() с несколькими
 *     строковыми аргументами (частый в этой базе паттерн вида
 *     cn("truncate w-full", condition ? "a" : "b")): литеральные части
 *     одного вызова cn() склеиваются в одну строку и проверяются как единый
 *     набор классов. Срабатывание: flex-1 или basis-0 вместе с truncate в
 *     одном className/cn(), без min-w-0 там же.
 *
 *  2. CSS (оба фронта): правило, в котором есть и overflow: hidden, и
 *     text-overflow: ellipsis (подпись truncate вне Tailwind-классов), но
 *     нет min-width: 0 в том же правиле.
 *
 * ЧТО ТЕСТ ЗАВЕДОМО НЕ ЛОВИТ (сжимаемость — свойство родителя и реального
 * layout, а не текста файла; ниже — не пробелы в реализации проверки, а
 * принципиальный предел статического текстового скана):
 *
 *  - truncate на элементе без буквального flex-1/basis-0 в том же
 *    className/cn() — когда flex/grid-контекст задаёт родитель, а сам
 *    элемент уже несёт свой min-w-0 (в этой базе так сделаны, например,
 *    PerformanceTable.tsx, AssistantPanel.tsx, WorkerPulse.tsx,
 *    HistoryTimeline.tsx). Это корректный и частый паттерн, но при
 *    регрессии (кто-то уберёт min-w-0 с самого элемента) тест этого не
 *    заметит — токена flex-1/basis-0 в этой же строке просто нет;
 *  - размеры и flex/grid-свойства, заданные в prop style={{...}} вместо
 *    className (пример — футер OfferCard.tsx: flex/flexWrap через style);
 *  - className, собранный не литералом и не через cn() (шаблон с рантайм-
 *    конкатенацией, classnames() из другого пакета, computed-значения);
 *  - реальную сжимаемость по layout: тест не рендерит DOM, не знает ни
 *    настоящую ширину контейнера, ни направление flex у родителя — только
 *    текстовую подпись дефекта в исходниках.
 *
 * Другими словами: это сигнатурный лint по тексту файлов, а не проверка
 * вёрстки. Он держит закрытым ровно тот класс регрессий, который поддаётся
 * текстовому поиску, и не годится как единственное доказательство того, что
 * где-то ничего не обрезается — для новых мест это по-прежнему решает
 * ревью верстки.
 */

const ROOTS = [
  resolve(__dirname, "../../.."),
  resolve(__dirname, "../../../../frontend-mini"),
];

function sourceFiles(dir: string, extension: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist" || entry === "tests") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full, extension));
    } else if (full.endsWith(extension)) {
      found.push(full);
    }
  }
  return found;
}

// ─── JS/TSX: className-литералы, включая литералы внутри cn(...) ──────────────

/** Значения className в одном атрибуте: только строковые/шаблонные литералы без cn(). */
function classNameLiterals(source: string): string[] {
  return [...source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)].map(
    (match) => match[1] ?? match[2] ?? "",
  );
}

/** Индекс символа сразу за строковым/шаблонным литералом, открытым в позиции at символом quote. */
function skipStringLiteral(source: string, at: number, quote: string): number {
  let i = at + 1;
  while (i < source.length) {
    if (source[i] === "\\") {
      i += 2;
      continue;
    }
    if (source[i] === quote) return i + 1;
    i++;
  }
  return i;
}

/** Индекс закрывающей ")", парной уже потреблённой открывающей — с учётом строк/шаблонов внутри вызова. */
function matchingParenEnd(source: string, start: number): number {
  let depth = 1;
  let i = start;
  while (i < source.length && depth > 0) {
    const ch = source[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      i = skipStringLiteral(source, i, ch);
      continue;
    }
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    i++;
  }
  return depth === 0 ? i - 1 : -1;
}

/**
 * Один вызов cn(...) часто собирает классы из нескольких строковых
 * аргументов (cn("font-... truncate", condition ? "a" : "b")). Литеральные
 * части одного вызова склеиваются в одну строку — этого достаточно для
 * сигнатурной проверки ниже; нелитеральные (тернарники, переменные) не
 * учитываются, как и раньше для обычного className.
 */
function cnCallLiterals(source: string): string[] {
  const literals: string[] = [];
  const callStart = /\bcn\(/g;
  let match: RegExpExecArray | null;
  while ((match = callStart.exec(source))) {
    const bodyStart = match.index + match[0].length;
    const bodyEnd = matchingParenEnd(source, bodyStart);
    if (bodyEnd === -1) continue;
    const body = source.slice(bodyStart, bodyEnd);
    const strings = [...body.matchAll(/"([^"]*)"|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? "");
    literals.push(strings.join(" "));
  }
  return literals;
}

/** Подпись дефекта: сжимающий класс + truncate в одном наборе классов, без min-w-0 там же. */
function classSignatureOffender(classes: string): boolean {
  const words = classes.split(/\s+/);
  const shrinks = words.includes("flex-1") || words.includes("basis-0");
  const clips = words.includes("truncate");
  return shrinks && clips && !words.includes("min-w-0");
}

// ─── CSS: overflow:hidden + text-overflow:ellipsis без min-width:0 в правиле ──

/** CSS-комментарии не участвуют в сигнатуре и могут содержать произвольные символы. */
function stripCssComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Индекс закрывающей "}", парной уже потреблённой открывающей. */
function matchingBraceEnd(source: string, start: number): number {
  let depth = 1;
  let i = start;
  while (i < source.length && depth > 0) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") depth--;
    i++;
  }
  return depth === 0 ? i - 1 : -1;
}

interface CssRule {
  selector: string;
  body: string;
}

/** Правила верхнего уровня и вложенные в @media/@supports/@keyframes — рекурсивно, плоским списком. */
function cssRules(source: string): CssRule[] {
  const rules: CssRule[] = [];
  let i = 0;
  while (i < source.length) {
    const open = source.indexOf("{", i);
    if (open === -1) break;
    const selector = source.slice(i, open).trim().replace(/\s+/g, " ");
    const close = matchingBraceEnd(source, open + 1);
    if (close === -1) break;
    const body = source.slice(open + 1, close);
    if (selector.startsWith("@")) {
      rules.push(...cssRules(body));
    } else if (selector) {
      rules.push({ selector, body });
    }
    i = close + 1;
  }
  return rules;
}

/** Подпись truncate в CSS: overflow:hidden + text-overflow:ellipsis без min-width:0 в том же правиле. */
function cssSignatureOffender(body: string): boolean {
  const hasOverflowHidden = /overflow\s*:\s*hidden\b/.test(body);
  const hasEllipsis = /text-overflow\s*:\s*ellipsis\b/.test(body);
  const hasMinWidthZero = /min-width\s*:\s*0(?:px)?\b/.test(body);
  return hasOverflowHidden && hasEllipsis && !hasMinWidthZero;
}

/**
 * Разобранные вручную исключения: сигнатура (overflow:hidden +
 * text-overflow:ellipsis без min-width:0 в правиле) у них есть буквально,
 * но для каждого перепроверен реальный родитель в разметке и в CSS —
 * это обычный блочный контейнер без display:flex/grid, поэтому
 * автоматический minimum-size flex/grid-элемента к нему не относится и
 * переполнение структурно невозможно (см. отчёт Task 6 для разбора).
 * Список специально короткий и точный: если родитель когда-нибудь станет
 * flex/grid-контейнером, это исключение не отследит такую правку
 * автоматически — при изменении родителя нужна ручная переоценка.
 */
const CSS_SIGNATURE_EXCLUSIONS: ReadonlyArray<{
  file: string;
  selector: string;
  reason: string;
}> = [
  {
    file: "src/features/operator/operator-mini-ledger.css",
    selector: ".mini-ledger-totals dd",
    reason:
      "dd лежит в обычном блочном div (.mini-ledger-totals > div: min-width: 0, без display) — не flex/grid-ребёнок.",
  },
  {
    file: "src/features/operator/operator-mini-ledger.css",
    selector: ".mini-ledger-cabinet__identity strong, .mini-ledger-cabinet__identity small",
    reason:
      "strong/small — обычные дети .mini-ledger-cabinet__identity (min-width: 0, без display); сам этот элемент — не flex/grid-контейнер для них.",
  },
  {
    file: "src/features/operator/operator-mini-ledger.css",
    selector: ".mini-ledger-funnel li > span",
    reason: "span лежит в li без display:flex/grid (.mini-ledger-funnel li: min-width: 0, без display) — не flex/grid-ребёнок.",
  },
];

function isKnownSafeCssMatch(root: string, file: string, selector: string): boolean {
  const relPath = relative(root, file);
  return CSS_SIGNATURE_EXCLUSIONS.some(
    (entry) => entry.file === relPath && entry.selector === selector,
  );
}

describe("сжимаемость обрезаемых строк", () => {
  it("не оставляет truncate на элементе, который не умеет сжиматься (className/cn())", () => {
    const offenders: string[] = [];

    for (const root of ROOTS) {
      for (const file of sourceFiles(join(root, "src"), ".tsx")) {
        const source = readFileSync(file, "utf8");
        const candidates = [...classNameLiterals(source), ...cnCallLiterals(source)];
        for (const classes of candidates) {
          if (classSignatureOffender(classes)) {
            offenders.push(`${relative(root, file)}: ${classes}`);
          }
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it("не оставляет CSS-подпись truncate (overflow+ellipsis) без min-width: 0 в том же правиле", () => {
    const offenders: string[] = [];

    for (const root of ROOTS) {
      for (const file of sourceFiles(join(root, "src"), ".css")) {
        const source = stripCssComments(readFileSync(file, "utf8"));
        for (const rule of cssRules(source)) {
          if (cssSignatureOffender(rule.body) && !isKnownSafeCssMatch(root, file, rule.selector)) {
            offenders.push(`${relative(root, file)} :: ${rule.selector}`);
          }
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
