export type ThemeName = "dark" | "light";

export const THEME_STORAGE_KEY = "sab-theme";

export function getInitialTheme(storage: Storage = window.localStorage): ThemeName {
  const stored = storage.getItem(THEME_STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "dark";
}

export function storeTheme(theme: ThemeName, storage: Storage = window.localStorage) {
  storage.setItem(THEME_STORAGE_KEY, theme);
}

