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
  // Сохраняем токен и роль в sessionStorage
  sessionStorage.setItem("tma_token", data.token);
  sessionStorage.setItem("tma_role", data.role);
  return data;
}

// Получение токена из sessionStorage
export function getStoredToken() {
  return sessionStorage.getItem("tma_token");
}

// Получение роли из sessionStorage
export function getStoredRole() {
  return sessionStorage.getItem("tma_role");
}

// Выход из сессии
export function logout() {
  sessionStorage.removeItem("tma_token");
  sessionStorage.removeItem("tma_role");
}
