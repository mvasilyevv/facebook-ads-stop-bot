// Получение initData из Telegram WebApp
export function getInitData() {
  return window.Telegram?.WebApp?.initData || "";
}

// Аутентификация на бэкенде через initData
export async function loginToBackend() {
  const initData = getInitData();
  const apiBase = import.meta.env.VITE_API_BASE || "/api";

  const resp = await fetch(`${apiBase}/tma/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || "Ошибка аутентификации");
  }

  const data = await resp.json();
  // localStorage переживает сворачивание/восстановление Telegram WebView,
  // sessionStorage в TWA нестабилен и часто очищается между сессиями.
  try {
    localStorage.setItem("tma_token", data.token);
    localStorage.setItem("tma_role", data.role);
  } catch {
    // Fallback: если localStorage недоступен (приватный режим), пишем в sessionStorage
    sessionStorage.setItem("tma_token", data.token);
    sessionStorage.setItem("tma_role", data.role);
  }
  return data;
}

// Получение токена: сначала пробуем localStorage, затем sessionStorage (fallback)
export function getStoredToken() {
  try {
    return localStorage.getItem("tma_token") || sessionStorage.getItem("tma_token");
  } catch {
    return sessionStorage.getItem("tma_token");
  }
}

// Получение роли: аналогично
export function getStoredRole() {
  try {
    return localStorage.getItem("tma_role") || sessionStorage.getItem("tma_role");
  } catch {
    return sessionStorage.getItem("tma_role");
  }
}

// Выход из сессии — чистим оба хранилища
export function logout() {
  try {
    localStorage.removeItem("tma_token");
    localStorage.removeItem("tma_role");
  } catch {
    // ignore
  }
  sessionStorage.removeItem("tma_token");
  sessionStorage.removeItem("tma_role");
}
