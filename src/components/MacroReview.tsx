import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, Progress, Row, Space, Tag, Tooltip, Typography } from 'antd';
import {
  FallOutlined,
  GlobalOutlined,
  MinusOutlined,
  QuestionOutlined,
  ReloadOutlined,
  RiseOutlined,
} from '@ant-design/icons';
import CenterShell from './common/CenterShell';
import DimensionCard from './common/DimensionCard';
import MarketRateTrend from './common/MarketRateTrend';
import NarrativeBadge from './common/NarrativeBadge';
import {
  getMacroReview,
  type MacroRegime,
  type MacroReviewResponse,
  type TearSheetDimension,
  type TearSheetSignal,
} from '../services/researchService';

const { Text, Paragraph } = Typography;

// 投资时钟象限配色：复苏=绿、过热=橙、滞胀=红、衰退=蓝灰、过渡/均衡=中性灰。
const regimeTheme = (name: string): { color: string; bg: string } => {
  if (name.includes('Goldilocks') || name.includes('复苏') || name.includes('软着陆')) return { color: '#3d9915', bg: '#f6ffed' };
  if (name.includes('Reflation') || name.includes('过热') || name.includes('扩张')) return { color: '#d4860a', bg: '#fff7e6' };
  if (name.includes('Stagflation') || name.includes('滞胀')) return { color: '#d42a2c', bg: '#fff1f0' };
  if (name.includes('Deflation') || name.includes('衰退') || name.includes('放缓')) return { color: '#2a5fd4', bg: '#f0f5ff' };
  return { color: '#8c8c8c', bg: '#fafafa' };
};

// 增长/通胀轴方向 → 箭头标签。扩张/升温=上行，放缓/回落=下行。
const axisTag = (label: string, dir: string) => {
  const up = dir === '扩张' || dir === '升温';
  const down = dir === '放缓' || dir === '回落';
  const color = up ? 'green' : down ? 'red' : 'default';
  const arrow = up ? '↑' : down ? '↓' : '→';
  return <Tag color={color} style={{ margin: 0 }}>{label} {dir} {arrow}</Tag>;
};

const RegimeBanner: React.FC<{ regime: MacroRegime }> = ({ regime }) => {
  if (regime.name === '数据不足') return null;
  const theme = regimeTheme(regime.name);
  return (
    <Card style={{ background: theme.bg, borderColor: theme.color }} bodyStyle={{ padding: 18 }}>
      <Row gutter={[24, 16]} align="middle">
        <Col xs={24} md={8}>
          <Text type="secondary" style={{ fontSize: 12, letterSpacing: 1 }}>投资时钟 · INVESTMENT CLOCK</Text>
          <div style={{ fontSize: 24, fontWeight: 800, color: theme.color, lineHeight: 1.3 }}>{regime.name}</div>
          <Space size={8} style={{ marginTop: 8 }} wrap>
            {axisTag('增长', regime.growth_axis)}
            {axisTag('通胀', regime.inflation_axis)}
          </Space>
          <Tooltip title="基于增长轴(股票/曲线/信用/波动率/就业) × 通胀轴(油价/CPI)的可用信号覆盖度与置信度">
            <div style={{ marginTop: 8, maxWidth: 220 }}>
              <Progress percent={Math.round(regime.confidence * 100)} size="small" strokeColor={theme.color} format={(p) => `置信 ${p}%`} />
            </div>
          </Tooltip>
        </Col>
        <Col xs={24} md={16}>
          <Paragraph style={{ fontSize: 14, marginBottom: 10 }}>{regime.playbook}</Paragraph>
          {regime.favored.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <Text strong style={{ color: '#3d9915', marginRight: 8 }}>超配</Text>
              {regime.favored.map((a) => <Tag key={a} color="green" style={{ marginBottom: 4 }}>{a}</Tag>)}
            </div>
          )}
          {regime.avoided.length > 0 && (
            <div>
              <Text strong style={{ color: '#d42a2c', marginRight: 8 }}>低配</Text>
              {regime.avoided.map((a) => <Tag key={a} color="red" style={{ marginBottom: 4 }}>{a}</Tag>)}
            </div>
          )}
        </Col>
      </Row>
    </Card>
  );
};

// 宏观维度对风险资产：bullish=利好(risk-on)，bearish=利空(risk-off)
const SIGNAL_META: Record<TearSheetSignal, { text: string; color: string; icon: React.ReactNode }> = {
  bullish: { text: '利好', color: '#3d9915', icon: <RiseOutlined /> },
  bearish: { text: '利空', color: '#d42a2c', icon: <FallOutlined /> },
  neutral: { text: '中性', color: '#cc8a00', icon: <MinusOutlined /> },
  insufficient: { text: '数据不足', color: '#8c8c8c', icon: <QuestionOutlined /> },
};

const VERDICT_COLOR: Record<string, string> = {
  风险偏好: '#3d9915',
  中性: '#cc8a00',
  避险: '#d42a2c',
  数据不足: '#8c8c8c',
};

const MacroReview: React.FC = () => {
  const [review, setReview] = useState<MacroReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReview(await getMacroReview());
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '宏观速判加载失败，请确认后端 API 已启动');
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
      eyebrow="MACRO REGIME"
      title={(
        <Space>
          <GlobalOutlined />
          宏观环境速判
        </Space>
      )}
      subtitle="投资时钟体制 · 美国宏观(市场/波动率/利率/实际利率/曲线/信用/美元/通胀/通胀预期/避险) + 中国宏观(沪深300/北向/人民币/中债)"
      actions={(
        <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      )}
      error={error}
      loading={loading && !review}
      loadingText="正在拉取标普500、美债、原油、黄金、VIX、收益率曲线、信用利差真实数据并判定风险偏好…"
      dataQuality={review?.data_quality}
    >
      {review && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {review.regime && <RegimeBanner regime={review.regime} />}

          <Card>
            <Row gutter={[24, 16]} align="middle">
              <Col xs={24} md={6} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: verdictColor }}>{review.overall_verdict}</div>
                <Text type="secondary">
                  风险偏好分 {review.overall_score > 0 ? '+' : ''}
                  {review.overall_score}
                </Text>
                <Progress percent={Math.round(review.confidence * 100)} size="small" strokeColor={verdictColor} format={(p) => `置信 ${p}%`} />
              </Col>
              <Col xs={24} md={18}>
                <Paragraph style={{ fontSize: 15, marginBottom: 8 }}>{review.narrative}</Paragraph>
                <NarrativeBadge provider={review.narrative_provider} />
              </Col>
            </Row>
          </Card>

          <MarketRateTrend sp500Series={review.sp500_series} us10ySeries={review.us10y_series} />

          <div>
            <Text strong style={{ fontSize: 13, color: '#8c8c8c', letterSpacing: 1 }}>美国宏观 · US MACRO</Text>
            <Row gutter={[16, 16]} style={{ marginTop: 8 }}>{review.dimensions.map(renderDimension)}</Row>
          </div>

          {review.china_dimensions && review.china_dimensions.length > 0 && (
            <div>
              <Space align="center" wrap style={{ marginBottom: 8 }}>
                <Text strong style={{ fontSize: 13, color: '#d42a2c', letterSpacing: 1 }}>中国宏观 · CHINA MACRO</Text>
                {review.china_read && <Text type="secondary" style={{ fontSize: 13 }}>{review.china_read}</Text>}
              </Space>
              <Row gutter={[16, 16]}>{review.china_dimensions.map(renderDimension)}</Row>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 12 }}>
            {review.disclaimer}
          </Text>
        </Space>
      )}
    </CenterShell>
  );
};

export default MacroReview;
