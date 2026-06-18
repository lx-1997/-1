import { useEffect } from 'react';
import { Modal } from 'antd';
import { Capacitor } from '@capacitor/core';
import { App as CapApp } from '@capacitor/app';
import { apiGet } from '../services/apiClient';

interface AndroidVersionInfo {
  version_code: number;
  version_name: string;
  apk_url: string;
  notes: string;
}

/**
 * 移动 App 壳更新检查（仅 Capacitor 原生环境生效，网页端为空操作）。
 * 内容更新随网站发布自动生效；此组件只负责壳本身（原生插件/签名变更）极少数的升级提示。
 */
const AppUpdateChecker: React.FC = () => {
  useEffect(() => {
    if (!Capacitor.isNativePlatform() || Capacitor.getPlatform() !== 'android') return;
    let cancelled = false;
    (async () => {
      try {
        const info = await CapApp.getInfo();
        const current = Number(info.build) || 1;
        const data = await apiGet<{ android: AndroidVersionInfo }>('/api/app/version');
        const latest = data.android;
        if (cancelled || !latest || latest.version_code <= current) return;
        Modal.confirm({
          title: `发现新版本 v${latest.version_name}`,
          content: latest.notes || '新版本包含原生功能改进，建议立即更新。',
          okText: '下载更新',
          cancelText: '稍后再说',
          onOk: () => {
            window.open(latest.apk_url, '_system');
          },
        });
      } catch {
        // 版本检查失败静默忽略，不影响使用
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
};

export default AppUpdateChecker;
