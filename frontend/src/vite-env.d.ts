/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API-ключ для X-API-Key (проброс из API_KEY бэка через run.sh). H5: bootstrap в main.tsx. */
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
