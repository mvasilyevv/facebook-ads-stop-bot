// Структурный locator: testid → fiber-role → aria → нормализованный текст fallback.
import { normalizeText } from './text.js';
import { getReactProps } from './fiber.js';

export interface BlockLookup {
  testid?: string;
  fiberRole?: string;
  aria?: string[];
  text?: string[];
}

export function findByTestId(testid: string, root: ParentNode = document): Element | null {
  return root.querySelector(`[data-testid="${CSS.escape(testid)}"]`);
}

export function findByAriaLabel(
  labels: string[],
  root: ParentNode = document,
): Element | null {
  const targets = new Set(labels.map(normalizeText));
  for (const el of Array.from(root.querySelectorAll('[aria-label]'))) {
    const aria = normalizeText(el.getAttribute('aria-label') || '');
    if (targets.has(aria)) return el;
  }
  return null;
}

export function findByFiberRole(role: string, root: ParentNode = document): Element | null {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
    const props = getReactProps(el);
    if (props && (props as any).role === role) return el;
  }
  return null;
}

export function findByNormalizedText(
  texts: string[],
  root: ParentNode = document,
): Element | null {
  const targets = new Set(texts.map(normalizeText));
  const walker = document.createTreeWalker(root as Node, NodeFilter.SHOW_ELEMENT);
  let cur = walker.currentNode as Element | null;
  while (cur) {
    const direct = Array.from(cur.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => normalizeText(n.textContent || ''))
      .join(' ')
      .trim();
    if (direct && targets.has(direct)) return cur;
    cur = walker.nextNode() as Element | null;
  }
  return null;
}

export function findBlock(spec: BlockLookup, root: ParentNode = document): Element | null {
  if (spec.testid) {
    const el = findByTestId(spec.testid, root);
    if (el) return el;
  }
  if (spec.fiberRole) {
    const el = findByFiberRole(spec.fiberRole, root);
    if (el) return el;
  }
  if (spec.aria?.length) {
    const el = findByAriaLabel(spec.aria, root);
    if (el) return el;
  }
  if (spec.text?.length) {
    const el = findByNormalizedText(spec.text, root);
    if (el) return el;
  }
  return null;
}
