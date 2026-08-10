import type React from 'react';
import { useMemo } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { useTheme } from 'next-themes';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

/** Build a comma-separated hsl() color from a space-separated CSS triplet like "193 100% 43%". */
function hslVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const raw = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  const parts = raw.split(/\s+/).filter(Boolean);
  if (parts.length === 3) {
    return `hsl(${parts[0]}, ${parts[1]}, ${parts[2]})`;
  }
  return fallback;
}

/**
 * antd theme adapter: follows the active CSS theme (light/dark), with text and
 * accent colors derived from the DSA design-system variables so antd components
 * stay legible on both light and dark backgrounds.
 */
export const AntdConfigProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { language } = useUiLanguage();
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';

  const antdThemeConfig = useMemo(() => {
    const algorithm = isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm;
    const primary = hslVar('--primary', 'hsl(193, 100%, 43%)');
    const foreground = hslVar('--foreground', 'hsl(228, 35%, 12%)');
    const muted = hslVar('--muted-foreground', 'hsl(224, 12%, 42%)');
    const container = hslVar('--card', isDark ? 'hsl(230, 24%, 10%)' : 'hsl(0, 0%, 100%)');
    const border = hslVar('--border', isDark ? 'hsl(226, 19%, 20%)' : 'hsl(217, 28%, 84%)');
    return {
      algorithm,
      token: {
        colorPrimary: primary,
        colorText: foreground,
        colorTextSecondary: muted,
        colorBgContainer: container,
        colorBgElevated: container,
        colorBorder: border,
        borderRadius: 12,
        fontSize: 13,
      },
      components: {
        Tabs: {
          itemColor: muted,
          itemSelectedColor: foreground,
          itemHoverColor: foreground,
          inkBarColor: primary,
          titleFontSize: 14,
        },
      },
    };
  }, [isDark]);

  return (
    <ConfigProvider locale={language === 'en' ? enUS : zhCN} theme={antdThemeConfig}>
      {children}
    </ConfigProvider>
  );
};
