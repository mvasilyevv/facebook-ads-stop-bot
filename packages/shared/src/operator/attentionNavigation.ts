const SAFE_ATTENTION_ROUTES = new Set([
  "/",
  "/actions",
  "/ads",
  "/incidents",
  "/analytics",
  "/settings",
  "/system/sources",
]);

const SAFE_ATTENTION_DETAIL =
  /^\/(?:ads|actions|incidents|cabinets)\/[A-Za-z0-9_-]{1,160}$/;

/**
 * Attention links are server-provided navigation hints, not arbitrary URLs.
 * Keep the boundary deliberately narrow so a malformed payload cannot create
 * an external, protocol-relative or script link in an operator surface.
 */
export function safeOperatorAttentionHref(href: string): string | null {
  if (SAFE_ATTENTION_ROUTES.has(href) || SAFE_ATTENTION_DETAIL.test(href)) {
    return href;
  }
  return null;
}
