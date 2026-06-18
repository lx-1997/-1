import React from 'react';
import { Alert } from 'antd';
import type { DataQuality } from '../../types';

/**
 * DataQualityBanner —— 统一的数据可信度提示条。
 *
 * 后端在每个会兜底/演示的接口出口写入 data_quality（live | degraded | mock）。
 * 本组件据此显式提醒用户「这是演示/降级数据，不能作为投资依据」。
 * level === 'live'（真实云端结果）时不渲染，保持界面干净。
 */
export interface DataQualityBannerProps {
  quality?: DataQuality | null;
  className?: string;
  style?: React.CSSProperties;
}

const LEVEL_ALERT_TYPE: Record<string, 'error' | 'warning'> = {
  mock: 'error',
  degraded: 'warning',
};

const DataQualityBanner: React.FC<DataQualityBannerProps> = ({ quality, className, style }) => {
  if (!quality || quality.level === 'live') {
    return null;
  }

  const type = LEVEL_ALERT_TYPE[quality.level] ?? 'warning';
  const label = quality.label || (quality.level === 'mock' ? '演示数据' : '降级兜底');

  return (
    <Alert
      className={className}
      style={{ marginBottom: 12, ...style }}
      type={type}
      showIcon
      message={label}
      description={
        (quality.detail || quality.reasons?.length) ? (
          <div>
            {quality.detail && <div>{quality.detail}</div>}
            {quality.reasons?.length ? (
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {quality.reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : undefined
      }
    />
  );
};

export default DataQualityBanner;
