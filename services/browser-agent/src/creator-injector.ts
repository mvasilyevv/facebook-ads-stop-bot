// Подгрузка creator-бандла и установка fbAgentEmit-биндинга в каждый контекст.
// Бандл собирается esbuild'ом в dist/creator-bundle.js (см. package.json bundle:creator).
import fs from 'node:fs';
import path from 'node:path';
import type { BrowserContext } from 'playwright';

const BUNDLE_PATH = path.resolve(__dirname, 'creator-bundle.js');

let cachedBundle: string | null = null;

function loadBundle(): string {
  if (cachedBundle) return cachedBundle;
  if (!fs.existsSync(BUNDLE_PATH)) {
    throw new Error(
      `creator-bundle.js не найден по пути ${BUNDLE_PATH}. Запустите npm run bundle:creator`,
    );
  }
  cachedBundle = fs.readFileSync(BUNDLE_PATH, 'utf8');
  return cachedBundle;
}

// Канал для событий из браузера в Node. Подписчики получают (event, payload)
// от runPlan и других внутренних эмиттеров.
export type CreatorEventListener = (event: string, payload: unknown) => void;

const contextListeners = new WeakMap<BrowserContext, Set<CreatorEventListener>>();

export function addCreatorEventListener(
  context: BrowserContext,
  listener: CreatorEventListener,
): () => void {
  let bag = contextListeners.get(context);
  if (!bag) {
    bag = new Set();
    contextListeners.set(context, bag);
  }
  bag.add(listener);
  return () => bag?.delete(listener);
}

function getListeners(context: BrowserContext): Set<CreatorEventListener> {
  return contextListeners.get(context) ?? new Set();
}

// Инжект бандла + биндинга. Идемпотентен по контексту: повторный вызов — no-op.
const injectedContexts = new WeakSet<BrowserContext>();

export async function injectCreator(context: BrowserContext): Promise<void> {
  if (injectedContexts.has(context)) return;
  // Защита от моков: в тестах контекст может не иметь exposeBinding/addInitScript.
  if (
    typeof (context as any).exposeBinding !== 'function' ||
    typeof context.addInitScript !== 'function'
  ) {
    return;
  }
  injectedContexts.add(context);

  // exposeBinding регистрирует window.fbAgentEmit, доступный в каждой новой странице.
  await context.exposeBinding(
    'fbAgentEmit',
    (_source, event: string, payload: unknown) => {
      for (const listener of getListeners(context)) {
        try {
          listener(event, payload);
        } catch {
          // Слушатели изолированы — не валим инжект из-за ошибки одного.
        }
      }
    },
  );

  // Бандл устанавливает window.__fbAgent в каждом документе до запуска скриптов страницы.
  await context.addInitScript({ content: loadBundle() });
}
