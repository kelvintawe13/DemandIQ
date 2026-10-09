import React, { createContext, useContext, useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { api, ApiException } from '../api/client';

type Theme = 'light' | 'dark';
type ColorScheme = 'ocean' | 'ash' | 'green' | 'blue' | 'yellow' | 'purple' | 'orange' | 'red' | 'pink' | 'custom';

/**
 * The platform opens in dark mode.
 *
 * Dark is the *default*, not a preference: a first-time visitor, a public page
 * and a signed-out session all paint dark before any stored choice is read. A
 * signed-in user who picked light keeps it — the re-hydration below overrides
 * this as soon as `/me/preferences` answers.
 */
const DEFAULT_THEME: Theme = 'dark';

interface ThemeContextValue {
  theme: Theme;
  colorScheme: ColorScheme;
  customColor: string;
  setTheme: (theme: Theme) => void;
  setColorScheme: (scheme: ColorScheme) => void;
  setCustomColor: (color: string) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: DEFAULT_THEME,
  colorScheme: 'ash',
  customColor: '#64748b',
  setTheme: () => undefined,
  setColorScheme: () => undefined,
  setCustomColor: () => undefined,
  toggleTheme: () => undefined
});

export function ThemeProvider({
  children,
  initialTheme = DEFAULT_THEME,
  initialColorScheme = 'ocean',
  initialCustomColor = '#64748b'



}: {children: React.ReactNode;initialTheme?: Theme;initialColorScheme?: ColorScheme;initialCustomColor?: string;}) {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [colorScheme, setColorScheme] = useState<ColorScheme>(initialColorScheme);
  const [customColor, setCustomColor] = useState<string>(initialCustomColor);
  const hydratedRef = useRef(false);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    
    // Apply color scheme classes
    root.classList.remove('color-scheme-ocean', 'color-scheme-ash', 'color-scheme-green', 'color-scheme-blue', 'color-scheme-yellow', 'color-scheme-purple', 'color-scheme-orange', 'color-scheme-red', 'color-scheme-pink', 'color-scheme-custom');
    root.classList.add(`color-scheme-${colorScheme}`);
    
    // Apply custom color if selected
    if (colorScheme === 'custom') {
      root.style.setProperty('--brand-500', customColor);
      root.style.setProperty('--brand-600', adjustColorBrightness(customColor, -10));
      root.style.setProperty('--brand-700', adjustColorBrightness(customColor, -20));
      root.style.setProperty('--brand-400', adjustColorBrightness(customColor, 10));
      root.style.setProperty('--brand-300', adjustColorBrightness(customColor, 20));
    }
    
    return () => {
      root.classList.remove('dark');
      root.classList.remove('color-scheme-ocean', 'color-scheme-ash', 'color-scheme-green', 'color-scheme-blue', 'color-scheme-yellow', 'color-scheme-purple', 'color-scheme-orange', 'color-scheme-red', 'color-scheme-pink', 'color-scheme-custom');
      if (colorScheme === 'custom') {
        root.style.removeProperty('--brand-500');
        root.style.removeProperty('--brand-600');
        root.style.removeProperty('--brand-700');
        root.style.removeProperty('--brand-400');
        root.style.removeProperty('--brand-300');
      }
    };
  }, [theme, colorScheme, customColor]);

  // Helper function to adjust color brightness
  function adjustColorBrightness(hex: string, percent: number): string {
    const num = parseInt(hex.replace('#', ''), 16);
    const amt = Math.round(2.55 * percent);
    const R = (num >> 16) + amt;
    const G = (num >> 8 & 0x00FF) + amt;
    const B = (num & 0x0000FF) + amt;
    return '#' + (0x1000000 + (R < 255 ? R < 1 ? 0 : R : 255) * 0x10000 + 
      (G < 255 ? G < 1 ? 0 : G : 255) * 0x100 + 
      (B < 255 ? B < 1 ? 0 : B : 255)).toString(16).slice(1);
  }

  // Re-hydrate from GET /api/v1/me/preferences on mount — only when signed in
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (!localStorage.getItem('demandiq_token')) return;
      } catch { return; }
      try {
        const prefs = await api.get<{ theme?: string; colorScheme?: string; customColor?: string }>('/api/v1/me/preferences');
        if (cancelled) return;
        const t = prefs.theme;
        const cs = prefs.colorScheme;
        const cc = prefs.customColor;
        
        if (t === 'light' || t === 'dark') {
          setTheme(t);
          hydratedRef.current = true;
        } else if (t === 'system') {
          const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)')?.matches;
          setTheme(prefersDark ? 'dark' : 'light');
          hydratedRef.current = true;
        }
        
        if (cs === 'ocean' || cs === 'ash' || cs === 'green' || cs === 'blue' || cs === 'yellow' || cs === 'purple' || cs === 'orange' || cs === 'red' || cs === 'pink' || cs === 'custom') {
          setColorScheme(cs);
        }
        
        if (cc) {
          setCustomColor(cc);
        }
      } catch {
        // keep initialTheme if offline/unauthorized
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const persistTheme = useCallback(async (next: Theme) => {
    try {
      if (!localStorage.getItem('demandiq_token')) return;
    } catch { return; }
    try {
      await api.patch('/api/v1/me/preferences', { theme: next });
    } catch (e) {
      if (e instanceof ApiException && e.status === 401) return;
      // network/offline — keep local value, Settings surface will show error if needed
    }
  }, []);

  const persistColorScheme = useCallback(async (next: ColorScheme) => {
    try {
      if (!localStorage.getItem('demandiq_token')) return;
    } catch { return; }
    try {
      await api.patch('/api/v1/me/preferences', { colorScheme: next });
    } catch (e) {
      if (e instanceof ApiException && e.status === 401) return;
      // network/offline — keep local value, Settings surface will show error if needed
    }
  }, []);

  const persistCustomColor = useCallback(async (next: string) => {
    try {
      if (!localStorage.getItem('demandiq_token')) return;
    } catch { return; }
    try {
      await api.patch('/api/v1/me/preferences', { customColor: next });
    } catch (e) {
      if (e instanceof ApiException && e.status === 401) return;
      // network/offline — keep local value, Settings surface will show error if needed
    }
  }, []);

  const setThemeAndPersist = useCallback((next: Theme) => {
    setTheme(next);
    void persistTheme(next);
  }, [persistTheme]);

  const setColorSchemeAndPersist = useCallback((next: ColorScheme) => {
    setColorScheme(next);
    void persistColorScheme(next);
  }, [persistColorScheme]);

  const setCustomColorAndPersist = useCallback((next: string) => {
    setCustomColor(next);
    void persistCustomColor(next);
  }, [persistCustomColor]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === 'dark' ? 'light' : 'dark';
      void persistTheme(next);
      return next;
    });
  }, [persistTheme]);

  const value = useMemo(
    () => ({
      theme,
      colorScheme,
      customColor,
      setTheme: setThemeAndPersist,
      setColorScheme: setColorSchemeAndPersist,
      setCustomColor: setCustomColorAndPersist,
      toggleTheme,
    }),
    [theme, colorScheme, customColor, setThemeAndPersist, setColorSchemeAndPersist, setCustomColorAndPersist, toggleTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}