import React, { createContext, useCallback, useContext, useLayoutEffect, useState } from 'react';

type ThemeMode = 'dark' | 'light';
export type AccentTheme = 'blue' | 'graphite' | 'purple' | 'green' | 'orange';

export const ACCENT_THEME_OPTIONS: ReadonlyArray<{
  value: AccentTheme;
  label: string;
  dark: string;
  light: string;
}> = [
  { value: 'blue', label: '经典蓝', dark: '#0A84FF', light: '#007AFF' },
  { value: 'graphite', label: '石墨', dark: '#8E8E93', light: '#636366' },
  { value: 'purple', label: '紫色', dark: '#BF5AF2', light: '#AF52DE' },
  { value: 'green', label: '绿色', dark: '#30D158', light: '#248A3D' },
  { value: 'orange', label: '橙色', dark: '#FF9F0A', light: '#C93400' },
];

interface ThemeContextValue {
  theme: ThemeMode;
  accentTheme: AccentTheme;
  toggleTheme: () => void;
  setTheme: (theme: ThemeMode) => void;
  setAccentTheme: (accent: AccentTheme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'light',
  accentTheme: 'blue',
  toggleTheme: () => {},
  setTheme: () => {},
  setAccentTheme: () => {},
});

// v3 将首次访问默认设为浅色；不再沿用 v2 的深色默认值。用户主动切换后仍会持久化。
const STORAGE_KEY = 'deepfocus.theme.v3';
const ACCENT_STORAGE_KEY = 'deepfocus.accent.v1';

function getInitialTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'light';
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;  // 仅尊重显式选择
  } catch {}
  return 'light';  // 默认浅色；不自动跟随系统，避免首次访问因设备偏好变色
}

function getInitialAccent(): AccentTheme {
  if (typeof window === 'undefined') return 'blue';
  try {
    const stored = window.localStorage.getItem(ACCENT_STORAGE_KEY);
    if (ACCENT_THEME_OPTIONS.some(option => option.value === stored)) return stored as AccentTheme;
  } catch {}
  return 'blue';
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setThemeState] = useState<ThemeMode>(getInitialTheme);
  const [accentTheme, setAccentThemeState] = useState<AccentTheme>(getInitialAccent);

  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { window.localStorage.setItem(STORAGE_KEY, theme); } catch {}
  }, [theme]);

  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-accent', accentTheme);
    try { window.localStorage.setItem(ACCENT_STORAGE_KEY, accentTheme); } catch {}
  }, [accentTheme]);

  const setTheme = useCallback((mode: ThemeMode) => {
    setThemeState(mode);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const setAccentTheme = useCallback((accent: AccentTheme) => {
    setAccentThemeState(accent);
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, accentTheme, toggleTheme, setTheme, setAccentTheme }}>
      {children}
    </ThemeContext.Provider>
  );
};

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
