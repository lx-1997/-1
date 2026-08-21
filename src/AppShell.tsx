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
  colorPrimary: '#2997ff',
  colorSuccess: '#30d158',
  colorWarning: '#ffd60a',
  colorError: '#ff453a',
  colorInfo: '#2997ff',
  colorText: '#f5f5f7',
  colorTextSecondary: '#a1a1a6',
  colorTextTertiary: '#6e6e73',
  colorTextQuaternary: '#515154',
  colorBorder: '#2c2d32',
  colorBorderSecondary: '#222329',
  colorBgLayout: '#0b0c0f',
  colorBgContainer: '#17181c',
  colorBgElevated: '#202126',
  colorBgSpotlight: '#2c2d32',
  colorFillAlter: '#121316',
  colorFill: 'rgba(255,255,255,0.06)',
  colorFillSecondary: 'rgba(255,255,255,0.04)',
  colorFillTertiary: 'rgba(255,255,255,0.02)',
  colorFillQuaternary: 'rgba(255,255,255,0.01)',
  borderRadius: 10,
  borderRadiusLG: 12,
  fontSize: 13,
  controlHeight: 36,
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
};

const lightToken = {
  colorPrimary: '#0071e3',
  colorSuccess: '#248a3d',
  colorWarning: '#b25000',
  colorError: '#d70015',
  colorInfo: '#0071e3',
  colorText: '#1d1d1f',
  colorTextSecondary: '#6e6e73',
  colorTextTertiary: '#86868b',
  colorTextQuaternary: '#aeaeb2',
  colorBorder: '#d2d2d7',
  colorBorderSecondary: '#e5e5ea',
  colorBgLayout: '#f5f5f7',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBgSpotlight: '#1d1d1f',
  colorFillAlter: '#f2f2f7',
  colorFill: 'rgba(0,0,0,0.04)',
  colorFillSecondary: 'rgba(0,0,0,0.03)',
  colorFillTertiary: 'rgba(0,0,0,0.02)',
  colorFillQuaternary: 'rgba(0,0,0,0.01)',
  borderRadius: 10,
  borderRadiusLG: 12,
  fontSize: 13,
  controlHeight: 36,
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
};

const darkComponents: any = {
  Button: {
    borderRadius: 8, controlHeight: 34, primaryShadow: 'none',
    defaultBg: '#202126', defaultBorderColor: '#2c2d32', defaultColor: '#f5f5f7',
    defaultHoverBg: '#2c2d32', defaultHoverBorderColor: '#45464d', defaultHoverColor: '#ffffff'
  },
  Card: { borderRadiusLG: 12, paddingLG: 20, colorBgContainer: '#17181c' },
  Menu: {
    itemBorderRadius: 8, itemHeight: 40,
    darkItemBg: 'transparent', darkItemColor: '#a1a1a6',
    darkItemHoverBg: 'rgba(255,255,255,0.04)',
    darkItemSelectedBg: 'rgba(41,151,255,0.14)', darkItemSelectedColor: '#2997ff',
    darkSubMenuItemBg: 'transparent'
  },
  Tabs: { horizontalMargin: '0 18px 0 0', titleFontSize: 14, inkBarColor: '#2997ff' },
  Table: { headerBg: '#121316', headerColor: '#a1a1a6', rowHoverBg: 'rgba(255,255,255,0.035)', borderColor: '#2c2d32' },
  Input: { activeBorderColor: '#2997ff', hoverBorderColor: '#45464d', colorBgContainer: '#17181c', colorBorder: '#2c2d32', colorText: '#f5f5f7', colorTextPlaceholder: '#6e6e73' },
  Select: { selectorBg: '#17181c', colorBorder: '#2c2d32', colorText: '#f5f5f7', optionSelectedBg: 'rgba(41,151,255,0.14)', optionActiveBg: 'rgba(255,255,255,0.045)' },
  Modal: { contentBg: '#17181c', headerBg: '#17181c' },
  Tooltip: { colorBgSpotlight: '#2c2d32', colorTextLightSolid: '#f5f5f7' },
  Dropdown: { colorBgElevated: '#202126' },
  Alert: {
    colorInfoBg: 'rgba(41,151,255,0.1)', colorInfoBorder: 'rgba(41,151,255,0.22)',
    colorSuccessBg: 'rgba(48,209,88,0.1)', colorSuccessBorder: 'rgba(48,209,88,0.22)',
    colorWarningBg: 'rgba(255,214,10,0.1)', colorWarningBorder: 'rgba(255,214,10,0.22)',
    colorErrorBg: 'rgba(255,69,58,0.1)', colorErrorBorder: 'rgba(255,69,58,0.22)'
  },
  Statistic: { contentFontSize: 24 },
  Tag: { defaultBg: '#202126', defaultColor: '#a1a1a6' }
};

const lightComponents: any = {
  Button: { borderRadius: 8, controlHeight: 34, primaryShadow: 'none' },
  Card: { borderRadiusLG: 12, paddingLG: 20 },
  Menu: {
    itemBorderRadius: 8, itemHeight: 40,
    darkItemBg: '#1d1d1f', darkItemColor: '#f5f5f7',
    darkItemHoverBg: 'rgba(255,255,255,0.08)', darkItemSelectedBg: 'rgba(0,113,227,0.2)',
    darkItemSelectedColor: '#ffffff', darkSubMenuItemBg: 'transparent'
  },
  Tabs: { horizontalMargin: '0 18px 0 0', titleFontSize: 14, inkBarColor: '#0071e3' },
  Table: { headerBg: '#f2f2f7', headerColor: '#6e6e73', rowHoverBg: 'rgba(0,0,0,0.025)', borderColor: '#d2d2d7' },
  Input: { activeBorderColor: '#0071e3', hoverBorderColor: '#aeaeb2', colorTextPlaceholder: '#86868b' },
  Select: { optionSelectedBg: 'rgba(0,113,227,0.1)', optionActiveBg: 'rgba(0,0,0,0.035)' },
  Modal: {},
  Tooltip: { colorBgSpotlight: '#1d1d1f', colorTextLightSolid: '#ffffff' },
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
