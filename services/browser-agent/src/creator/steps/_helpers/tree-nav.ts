// Хелперы для работы с левой панелью (дерево кампаний/адсетов/объявлений).
import { normalizeText } from '../../text.js';

export function listTreeNodeNames(role: string): string[] {
  const nodes = Array.from(
    document.querySelectorAll<HTMLElement>(
      `[data-tree-role="${role}"], [data-testid="${role}-node"]`,
    ),
  );
  return nodes
    .map((el) => (el.getAttribute('data-name') || el.textContent || '').trim())
    .filter(Boolean);
}

export function findTreeNodeByName(role: string, name: string): HTMLElement | null {
  const target = normalizeText(name);
  const nodes = Array.from(
    document.querySelectorAll<HTMLElement>(
      `[data-tree-role="${role}"], [data-testid="${role}-node"]`,
    ),
  );
  for (const node of nodes) {
    const txt = normalizeText(node.getAttribute('data-name') || node.textContent || '');
    if (txt === target) return node;
  }
  return null;
}
