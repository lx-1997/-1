import React from 'react';
import { App as AntdApp, ConfigProvider, theme as antTheme } from 'antd';
import { useTheme } from '../context/ThemeContext';
import MacroDashboard from './MacroDashboard';

export default function MacroStandalone() {
  const { theme, toggleTheme } = useTheme();
  return <ConfigProvider theme={{ algorithm: theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm, token: { colorPrimary: '#1677ff' } }}>
    <AntdApp>
      <main style={{ maxWidth: 1440, margin: '0 auto', padding: '16px 12px', minHeight: '100vh', background: 'var(--surface)', color: 'var(--text)' }}>
        <nav aria-label="宏观雷达导航" style={{ display: 'flex', flexWrap: 'wrap', gap: 20, marginBottom: 20 }}>
          <a href="/">返回金融终端</a>
          <a href="/macro?tab=study">洞察回测</a>
          <button type="button" onClick={toggleTheme}>切换主题</button>
        </nav>
        <MacroDashboard />
      </main>
    </AntdApp>
  </ConfigProvider>;
}
