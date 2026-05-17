// Чтение React internals (__reactFiber$* / __reactProps$*) по динамическому ключу.
function findKey(el: Element, prefix: string): string | null {
  for (const key of Object.keys(el)) {
    if (key.startsWith(prefix)) return key;
  }
  return null;
}

export function getFiber(el: Element): unknown {
  const key = findKey(el, '__reactFiber$');
  return key ? (el as any)[key] : null;
}

export function getReactProps(el: Element): Record<string, unknown> | null {
  const key = findKey(el, '__reactProps$');
  return key ? ((el as any)[key] as Record<string, unknown>) : null;
}

export function walkUp(
  el: Element,
  predicate: (n: Element) => boolean,
  maxDepth = 12,
): Element | null {
  let cur: Element | null = el;
  let depth = 0;
  while (cur && depth < maxDepth) {
    if (predicate(cur)) return cur;
    cur = cur.parentElement;
    depth++;
  }
  return null;
}
