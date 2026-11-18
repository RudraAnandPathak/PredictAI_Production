const STORAGE_KEY = 'predictai.theme';
export const THEMES = Object.freeze(['dark', 'light']);
const THEME_COLOR = Object.freeze({ dark: '#0a0a0f', light: '#f5f5fa' });

export function getStoredTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return THEMES.includes(value) ? value : null;
  } catch {
    return null; // storage can be unavailable (private mode, blocked cookies)
  }
}

export function storeTheme(theme) {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* preference simply will not persist */
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
}

/** Dark is the brand default; a stored choice wins. */
export function initTheme() {
  const theme = getStoredTheme() ?? 'dark';
  applyTheme(theme);
  return theme;
}
