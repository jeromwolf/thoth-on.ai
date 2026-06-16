/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  readonly VITE_X_ROLE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
