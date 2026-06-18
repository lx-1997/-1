import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Progress, Row, Space, Statistic, Typography } from 'antd';
import {
  FallOutlined,
  MinusOutlined,
  QuestionOutlined,
  ReloadOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import CenterShell from './common/CenterShell';
import DimensionCard from './common/DimensionCard';
import MarketRateTrend from './common/MarketRateTrend';
import NarrativeBadge from './common/NarrativeBadge';
import {
  getPortfolioReview,
  type PortfolioReviewResponse,
  type TearSheetDimension,
  type TearSheetSignal,
} from '../services/researchService';

const { Text, Paragraph } = Typography;

// 组合层语义：健康 / 关注 / 高风险 / 不适用
const SIGNAL_META: Record<TearSheetSignal, { text: string; color: string; icon: React.ReactNode }> = {
  bullish: { text: '健康', color: '#3d9915', icon: <RiseOutlined /> },
  bearish: { text: '高风险', color: '#d42a2c', icon: <FallOutlined /> },
  neutral: { text: '关注', color: '#cc8a00', icon: <MinusOutlined /> },
  insufficient: { text: '不适用', color: '#8c8c8c', icon: <QuestionOutlined /> },
};

const VERDICT_COLOR: Record<string, string> = {
  稳健: '#3d9915',
  需关注: '#cc8a00',
  高风险: '#d42a2c',
  空仓: '#8c8c8c',
};

const PortfolioReview: React.FC = () => {
  const [review, setReview] = useState<PortfolioReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReview(await getPortfolioReview());
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '组合速判加载失败，请确认后端 API 已启动');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const verdictColor = review ? VERDICT_COLOR[review.overall_verdict] || '#8c8c8c' : '#8c8c8c';

  const renderDimension = (d: TearSheetDimension) => (
    <Col xs={24} md={12} key={d.key}>
      <DimensionCard dim={d} meta={SIGNAL_META[d.signal]} />
    </Col>
  );

  return (
    <CenterShell
      eyebrow="PORTFOLIO REVIEW"
      title={(
        <Space>
          <SafetyCertificateOutlined />
          组合风险速判
        </Space>
      )}
      subtitle="买方视角 · 集中度 / 行业敞口 / 回撤 / 止损纪律"
      actions={(
        <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      )}
      error={error}
      loading={loading && !review}
      loadingText="正在汇总本地持仓并逐项风控校验…"
      dataQuality={review?.data_quality}
    >
      {review && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card>
            <Row gutter={[24, 16]} align="middle">
              <Col xs={24} md={6} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: verdictColor }}>{review.overall_verdict}</div>
                <Text type="secondary">组合速判 · 稳健度 {review.risk_score}/100</Text>
                <Progress percent={review.risk_score} size="small" strokeColor={verdictColor} />
              </Col>
              <Col xs={24} md={18}>
                <Paragraph style={{ fontSize: 15, marginBottom: 8 }}>{review.narrative}</Paragraph>
                <div style={{ marginBottom: 8 }}>
                  <NarrativeBadge provider={review.narrative_provider} />
                </div>
                <Space size={24} wrap>
                  <Statistic title="持仓数" value={review.position_count} />
                  {typeof review.total_value === 'number' && (
                    <Statistic title="组合市值" value={review.total_value} precision={0} />
                  )}
                  {typeof review.total_pnl_pct === 'number' && (
                    <Statistic
                      title="累计盈亏"
                      value={review.total_pnl_pct}
                      precision={1}
                      suffix="%"
                      valueStyle={{ color: review.total_pnl_pct >= 0 ? '#d42a2c' : '#3d9915' }}
                    />
                  )}
                </Space>
              </Col>
            </Row>
          </Card>

          {review.alerts.length > 0 && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message="风控告警"
              description={(
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {review.alerts.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              )}
            />
          )}

          <MarketRateTrend
            sp500Series={review.sp500_series}
            us10ySeries={review.us10y_series}
            title="市场与利率背景 · 真实数据"
          />

          <Row gutter={[16, 16]}>{review.dimensions.map(renderDimension)}</Row>

          <Text type="secondary" style={{ fontSize: 12 }}>
            {review.disclaimer}
          </Text>
        </Space>
      )}
    </CenterShell>
  );
};

export default PortfolioReview;
