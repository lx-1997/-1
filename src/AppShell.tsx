// 完整版 App 外壳：把 antd（ConfigProvider/主题 token）+ i18n + App 全收在这里。
// index.tsx 用 React.lazy 异步加载本模块——终端独占版永不加载它，于是 antd / i18n / 整站代码
// 都不进终端首屏主包，显著减小体积、加快加载。
import './i18n';
import React from 'react';
import { App as AntdApp, ConfigProvider, theme as antTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useTheme } from './context/ThemeContext';
import App from './App';

const darkToken = {
  colorPrimary: '#10a37f',
  colorSuccess: '#10b981',
  colorWarning: '#f59e0b',
  colorError: '#ef4444',
  colorInfo: '#3b82f6',
  colorText: '#ececec',
  colorTextSecondary: '#9b9b9b',
  colorTextTertiary: '#6b6b6b',
  colorTextQuaternary: '#4a4a4a',
  colorBorder: '#2d2d2d',
  colorBorderSecondary: '#252525',
  colorBgLayout: '#0f0f0f',
  colorBgContainer: '#1a1a1a',
  colorBgElevated: '#242424',
  colorBgSpotlight: '#2d2d2d',
  colorFillAlter: '#141414',
  colorFill: 'rgba(255,255,255,0.06)',
  colorFillSecondary: 'rgba(255,255,255,0.04)',
  colorFillTertiary: 'rgba(255,255,255,0.02)',
  colorFillQuaternary: 'rgba(255,255,255,0.01)',
  borderRadius: 8,
  borderRadiusLG: 10,
  fontSize: 13,
  controlHeight: 36,
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
};

const lightToken = {
  colorPrimary: '#0f766e',
  colorSuccess: '#12805c',
  colorWarning: '#b7791f',
  colorError: '#c43e3e',
  colorInfo: '#2563eb',
  colorText: '#111827',
  colorTextSecondary: '#5f6875',
  colorTextTertiary: '#8a94a3',
  colorTextQuaternary: '#b0b8c1',
  colorBorder: '#d7dde5',
  colorBorderSecondary: '#e6eaf0',
  colorBgLayout: '#f5f5f7',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBgSpotlight: '#1a1a1a',
  colorFillAlter: '#f8fafc',
  colorFill: 'rgba(0,0,0,0.04)',
  colorFillSecondary: 'rgba(0,0,0,0.03)',
  colorFillTertiary: 'rgba(0,0,0,0.02)',
  colorFillQuaternary: 'rgba(0,0,0,0.01)',
  borderRadius: 8,
  borderRadiusLG: 10,
  fontSize: 13,
  controlHeight: 36,
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
};

const darkComponents: any = {
  Button: {
    borderRadius: 8, controlHeight: 34, primaryShadow: 'none',
    defaultBg: '#242424', defaultBorderColor: '#2d2d2d', defaultColor: '#ececec',
    defaultHoverBg: '#2d2d2d', defaultHoverBorderColor: '#3d3d3d', defaultHoverColor: '#ffffff'
  },
  Card: { borderRadiusLG: 10, paddingLG: 20, colorBgContainer: '#1a1a1a' },
  Menu: {
    itemBorderRadius: 8, itemHeight: 40,
    darkItemBg: 'transparent', darkItemColor: '#9b9b9b',
    darkItemHoverBg: 'rgba(255,255,255,0.04)',
    darkItemSelectedBg: 'rgba(16,163,127,0.12)', darkItemSelectedColor: '#10a37f',
    darkSubMenuItemBg: 'transparent'
  },
  Tabs: { horizontalMargin: '0 18px 0 0', titleFontSize: 14, inkBarColor: '#10a37f' },
  Table: { headerBg: '#141414', headerColor: '#9b9b9b', rowHoverBg: 'rgba(255,255,255,0.03)', borderColor: '#2d2d2d' },
  Input: { activeBorderColor: '#10a37f', hoverBorderColor: '#3d3d3d', colorBgContainer: '#1a1a1a', colorBorder: '#2d2d2d', colorText: '#ececec', colorTextPlaceholder: '#6b6b6b' },
  Select: { selectorBg: '#1a1a1a', colorBorder: '#2d2d2d', colorText: '#ececec', optionSelectedBg: 'rgba(16,163,127,0.12)', optionActiveBg: 'rgba(255,255,255,0.04)' },
  Modal: { contentBg: '#1a1a1a', headerBg: '#1a1a1a' },
  Tooltip: { colorBgSpotlight: '#2d2d2d', colorTextLightSolid: '#ececec' },
  Dropdown: { colorBgElevated: '#242424' },
  Alert: {
    colorInfoBg: 'rgba(59,130,246,0.08)', colorInfoBorder: 'rgba(59,130,246,0.2)',
    colorSuccessBg: 'rgba(16,185,129,0.08)', colorSuccessBorder: 'rgba(16,185,129,0.2)',
    colorWarningBg: 'rgba(245,158,11,0.08)', colorWarningBorder: 'rgba(245,158,11,0.2)',
    colorErrorBg: 'rgba(239,68,68,0.08)', colorErrorBorder: 'rgba(239,68,68,0.2)'
  },
  Statistic: { contentFontSize: 24 },
  Tag: { defaultBg: '#242424', defaultColor: '#9b9b9b' }
};

const lightComponents: any = {
  Button: { borderRadius: 8, controlHeight: 34, primaryShadow: 'none' },
  Card: { borderRadiusLG: 10, paddingLG: 20 },
  Menu: {
    itemBorderRadius: 8, itemHeight: 40,
    darkItemBg: '#111827', darkItemColor: '#d8e1e7',
    darkItemHoverBg: 'rgba(255,255,255,0.08)', darkItemSelectedBg: 'rgba(15,118,110,0.18)',
    darkItemSelectedColor: '#ffffff', darkSubMenuItemBg: 'transparent'
  },
  Tabs: { horizontalMargin: '0 18px 0 0', titleFontSize: 14, inkBarColor: '#0f766e' },
  Table: { headerBg: '#f6f8fa', headerColor: '#5f6875', rowHoverBg: 'rgba(0,0,0,0.02)', borderColor: '#d7dde5' },
  Input: { activeBorderColor: '#0f766e', hoverBorderColor: '#b0b8c1', colorTextPlaceholder: '#8a94a3' },
  Select: { optionSelectedBg: 'rgba(15,118,110,0.1)', optionActiveBg: 'rgba(0,0,0,0.03)' },
  Modal: {},
  Tooltip: { colorBgSpotlight: '#1a1a1a', colorTextLightSolid: '#ffffff' },
  Dropdown: {},
  Alert: {},
  Statistic: { contentFontSize: 24 },
  Tag: {}
};

const AppShell: React.FC = () => {
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: isDark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: isDark ? darkToken : lightToken,
        components: isDark ? darkComponents : lightComponents
      }}
    >
      <AntdApp><App /></AntdApp>
    </ConfigProvider>
  );
};

export default AppShell;
