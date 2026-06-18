/**
 * 设计系统 - Design Tokens
 * 
 * 统一管理颜色、字体、间距、阴影等设计属性
 * 确保整个应用视觉一致性
 */

// ==================== 颜色系统 ====================
export const colors = {
  // 品牌色
  primary: '#1890ff',
  primaryHover: '#40a9ff',
  primaryActive: '#096dd9',
  primaryLight: '#e6f7ff',
  
  // 语义色（A股：红涨绿跌）
  positive: '#f5222d',      // 涨（红色）
  positiveLight: '#fff1f0',
  negative: '#52c41a',      // 跌（绿色）
  negativeLight: '#f6ffed',
  
  // 中性色
  textPrimary: '#262626',
  textSecondary: '#8c8c8c',
  textDisabled: '#bfbfbf',
  textPlaceholder: '#bfbfbf',
  
  // 背景色
  bgPrimary: '#ffffff',
  bgSecondary: '#f5f5f5',
  bgTertiary: '#fafafa',
  bgElevated: '#ffffff',
  
  // 边框色
  borderDefault: '#d9d9d9',
  borderLight: '#e8e8e8',
  borderHeavy: '#434343',
  
  // 功能色
  success: '#52c41a',
  warning: '#faad14',
  error: '#f5222d',
  info: '#1890ff',
  
  // 侧边栏（深色主题）
  sidebarBg: '#001529',
  sidebarBgLight: '#002140',
  sidebarText: '#ffffff',
  sidebarTextSecondary: 'rgba(255, 255, 255, 0.65)',
};

// ==================== 字体系统 ====================
export const typography = {
  fontFamily: {
    base: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    mono: 'Menlo, Monaco, "Courier New", monospace',
    chinese: 'PingFang SC, Microsoft YaHei, sans-serif',
  },
  
  fontSize: {
    xs: 12,
    sm: 14,
    base: 16,
    lg: 18,
    xl: 20,
    xxl: 24,
    xxxl: 32,
  },
  
  fontWeight: {
    regular: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
  
  lineHeight: {
    tight: 1.25,
    base: 1.5,
    relaxed: 1.75,
  },
};

// ==================== 间距系统 ====================
export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
  xxxl: 64,
};

// ==================== 圆角系统 ====================
export const borderRadius = {
  sm: 2,
  base: 4,
  md: 8,
  lg: 12,
  xl: 16,
  round: 9999,
};

// ==================== 阴影系统 ====================
export const shadows = {
  sm: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px 0 rgba(0, 0, 0, 0.02)',
  base: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px 0 rgba(0, 0, 0, 0.02)',
  md: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
  lg: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
  xl: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
};

// ==================== 布局系统 ====================
export const layout = {
  sidebarWidth: 240,
  sidebarCollapsedWidth: 80,
  headerHeight: 64,
  composerMaxHeight: 200,
  composerMinHeight: 120,
};

// ==================== 动画系统 ====================
export const animations = {
  duration: {
    fast: 150,
    normal: 300,
    slow: 500,
  },
  
  easing: {
    easeInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
    easeOut: 'cubic-bezier(0, 0, 0.2, 1)',
    easeIn: 'cubic-bezier(0.4, 0, 1, 1)',
  },
};

// ==================== Z-index 系统 ====================
export const zIndex = {
  sidebar: 100,
  header: 200,
  modal: 1000,
  tooltip: 1100,
  notification: 1200,
};

// ==================== 断点系统（响应式）====================
export const breakpoints = {
  xs: 480,
  sm: 576,
  md: 768,
  lg: 992,
  xl: 1200,
  xxl: 1600,
};

// ==================== 导出默认主题 ====================
export const theme = {
  colors,
  typography,
  spacing,
  borderRadius,
  shadows,
  layout,
  animations,
  zIndex,
  breakpoints,
};

export default theme;
