import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, Progress, Row, Space, Tag, Typography } from 'antd';
import {
  ArrowRightOutlined,
  GlobalOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SunOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import type { ViewType } from '../types';
import CenterShell from './common/CenterShell';
import DataQualityBanner from './common/DataQualityBanner';
import NarrativeBadge from './common/NarrativeBadge';
import MarketRateTrend from './common/MarketRateTrend';
import { getBriefing, type BriefingResponse } from '../services/researchService';

const { Text, Paragraph, Title } = Typography;

const MACRO_COLOR: Record<string, string> = {
  风险偏好: '#3d9915',
  中性: '#cc8a00',
  避险: '#d42a2c',
  数据不足: '#8c8c8c',
};
const PORT_COLOR: Record<string, string> = {
  稳健: '#3d9915',
  需关注: '#cc8a00',
  高风险: '#d42a2c',
  空仓: '#8c8c8c',
};

interface BriefingProps {
  onViewChange: (view: ViewType) => void;
  symbols?: string[];
}

const Briefing: React.FC<BriefingProps> = ({ onViewChange, symbols }) => {
  const [data, setData] = useState<BriefingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const symbolsKey = (symbols || []).join(',');
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getBriefing(symbolsKey ? symbolsKey.split(',') : undefined));
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '晨报加载失败，请确认后端 API 已启动');
    } finally {
      setLoading(false);
    }
  }, [symbolsKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const macroColor = data ? MACRO_COLOR[data.macro_verdict] || '#8c8c8c' : '#8c8c8c';
  const portColor = data ? PORT_COLOR[data.portfolio_verdict] || '#8c8c8c' : '#8c8c8c';
  const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });

  return (
    <CenterShell
      eyebrow="MORNING BRIEFING"
      title={(
        <Space>
          <SunOutlined />
          投研晨报
        </Space>
      )}
      subtitle={`${today} · 宏观 × 组合 多引擎聚合`}
      actions={(
        <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      )}
      error={error}
      loading={loading && !data}
      loadingText="正在聚合宏观环境与组合风险，生成今日晨报…"
      dataQuality={data?.data_quality}
    >
      {data && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card style={{ borderLeft: `4px solid ${macroColor}` }}>
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Space size={8} wrap>
                <Tag color={macroColor}>宏观 · {data.macro_verdict}</Tag>
                <Tag color={portColor}>组合 · {data.portfolio_verdict}</Tag>
              </Space>
              <Title level={4} style={{ margin: 0, fontWeight: 600, lineHeight: 1.7 }}>
                {data.headline}
              </Title>
              <NarrativeBadge provider={data.headline_provider} />
            </Space>
          </Card>

          <MarketRateTrend sp500Series={data.macro.sp500_series} us10ySeries={data.macro.us10y_series} />

          <Row gutter={[16, 16]}>
            <Col xs={24} md={12}>
              <Card
                size="small"
                style={{ height: '100%', borderTop: `3px solid ${macroColor}` }}
                title={(
                  <Space>
                    <GlobalOutlined />
                    宏观环境
                  </Space>
                )}
                extra={(
                  <Button type="link" size="small" onClick={() => onViewChange('macro-review')}>
                    查看完整 <ArrowRightOutlined />
                  </Button>
                )}
              >
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: macroColor }}>
                    {data.macro_verdict}
                    <Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}>
                      风险偏好分 {data.macro.overall_score > 0 ? '+' : ''}
                      {data.macro.overall_score}
                    </Text>
                  </div>
                  <Paragraph type="secondary" style={{ marginBottom: 0 }} ellipsis={{ rows: 3 }}>
                    {data.macro.narrative}
                  </Paragraph>
                  <DataQualityBanner quality={data.macro.data_quality} />
                </Space>
              </Card>
            </Col>
            <Col xs={24} md={12}>
              <Card
                size="small"
                style={{ height: '100%', borderTop: `3px solid ${portColor}` }}
                title={(
                  <Space>
                    <SafetyCertificateOutlined />
                    组合风险
                  </Space>
                )}
                extra={(
                  <Button type="link" size="small" onClick={() => onViewChange('portfolio-review')}>
                    查看完整 <ArrowRightOutlined />
                  </Button>
                )}
              >
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: portColor }}>
                    {data.portfolio_verdict}
                    <Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}>
                      稳健度 {data.portfolio.risk_score}/100 · 持仓 {data.portfolio.position_count}
                    </Text>
                  </div>
                  <Progress
                    percent={data.portfolio.risk_score}
                    size="small"
                    strokeColor={portColor}
                    showInfo={false}
                  />
                  <Paragraph type="secondary" style={{ marginBottom: 0 }} ellipsis={{ rows: 2 }}>
                    {data.portfolio.narrative}
                  </Paragraph>
                  <DataQualityBanner quality={data.portfolio.data_quality} />
                </Space>
              </Card>
            </Col>
          </Row>

          {data.watchlist && data.watchlist.total > 0 && (
            <Card
              size="small"
              title={(
                <Space>
                  <UnorderedListOutlined />
                  自选观察 · 行业暴露
                </Space>
              )}
            >
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Paragraph style={{ marginBottom: 0 }}>{data.watchlist.note}</Paragraph>
                {data.watchlist.sectors.length > 0 && (
                  <Space size={8} wrap>
                    {data.watchlist.sectors.map((b, i) => (
                      <Tag key={i} color="#1769aa">
                        {b.sector} · {b.count}（{b.pct}%）
                      </Tag>
                    ))}
                  </Space>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  覆盖 {data.watchlist.covered}/{data.watchlist.total} 个标的（仅标普500 成分可解析 GICS 行业）
                </Text>
                <DataQualityBanner quality={data.watchlist.data_quality} />
              </Space>
            </Card>
          )}

          <Text type="secondary" style={{ fontSize: 12 }}>
            {data.disclaimer}
          </Text>
        </Space>
      )}
    </CenterShell>
  );
};

export default Briefing;
