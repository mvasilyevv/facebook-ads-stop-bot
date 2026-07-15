/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Внешний HTTPS-адрес защищённого рабочего стола Vision. */
  readonly VITE_REMOTE_DESKTOP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
