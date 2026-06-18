import React from 'react';
import { Card, Progress, Space, Tag, Typography } from 'antd';
import { LinkOutlined } from '@ant-design/icons';
import DataQualityBanner from './DataQualityBanner';
import type { TearSheetDimension } from '../../services/researchService';

const { Text } = Typography;

export interface SignalMeta {
  text: string;
  color: string;
  icon: React.ReactNode;
}

interface DimensionCardProps {
  dim: TearSheetDimension;
  meta: SignalMeta;
  /** 是否展示置信度进度条（个股速判卡用，组合/宏观不用）。 */
  showConfidence?: boolean;
}

/**
 * 共享的速判维度卡：信号 + 证据 + 置信度 + 可点击来源 + 数据可信度。
 * 个股速览 / 组合速判 / 宏观速判三处复用（消除重复 renderDimension）。
 * sources 体现 claim↔证据↔来源闭环：真实 github 数据维度可点击查看原始数据。
 */
const DimensionCard: React.FC<DimensionCardProps> = ({ dim, meta, showConfidence }) => (
  <Card size="small" style={{ height: '100%', borderLeft: `3px solid ${meta.color}` }}>
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text strong>{dim.label}</Text>
        <Tag color={meta.color} icon={meta.icon}>
          {meta.text}
        </Tag>
      </div>
      <Text style={{ fontSize: 15 }}>{dim.headline}</Text>
      {dim.evidence.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-muted)' }}>
          {dim.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      {showConfidence && (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            置信度
          </Text>
          <Progress percent={Math.round(dim.confidence * 100)} size="small" strokeColor={meta.color} />
        </div>
      )}
      {dim.sources && dim.sources.length > 0 && (
        <div style={{ fontSize: 12, lineHeight: 1.9 }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 4 }}>
            来源 ·
          </Text>
          {dim.sources.map((s, i) =>
            s.url ? (
              <a
                key={i}
                href={s.url}
                target="_blank"
                rel="noreferrer"
                style={{ marginRight: 10, whiteSpace: 'nowrap' }}
              >
                <LinkOutlined /> {s.label}
              </a>
            ) : (
              <Text key={i} type="secondary" style={{ marginRight: 10, fontSize: 12 }}>
                {s.label}
              </Text>
            ),
          )}
        </div>
      )}
      <DataQualityBanner quality={dim.data_quality} />
    </Space>
  </Card>
);

export default DimensionCard;
