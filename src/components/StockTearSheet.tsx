import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, Progress, Row, Space, Typography } from 'antd';
import {
  ArrowLeftOutlined,
  ExperimentOutlined,
  FallOutlined,
  MinusOutlined,
  QuestionOutlined,
  ReloadOutlined,
  RiseOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { Stock, ViewType } from '../types';
import CenterShell from './common/CenterShell';
import DimensionCard from './common/DimensionCard';
import NarrativeBadge from './common/NarrativeBadge';
import { formatGeneratedAt } from '../utils/datetime';
import MarketRateTrend from './common/MarketRateTrend';
import PriceTrend from './common/PriceTrend';
import {
  getTearSheet,
  type TearSheetDimension,
  type TearSheetResponse,
  type TearSheetSignal,
} from '../services/researchService';

const { Text, Paragraph } = Typography;

// 机构级分组：技术面 / 基本面 / 市场环境，让多维一页纸像专业终端而非平铺。
const DIM_GROUPS: { title: string; keys: string[] }[] = [
  { title: '技术面', keys: ['momentum', 'options', 'fund_flow'] },
  { title: '基本面', keys: ['catalyst', 'scale', 'valuation', 'consensus'] },
  { title: '市场环境', keys: ['market', 'macro'] },
];

const SIGNAL_META: Record<TearSheetSignal, { text: string; color: string; icon: React.ReactNode }> = {
  bullish: { text: '看多', color: '#3d9915', icon: <RiseOutlined /> },
  bearish: { text: '看空', color: '#d42a2c', icon: <FallOutlined /> },
  neutral: { text: '中性', color: '#cc8a00', icon: <MinusOutlined /> },
  insufficient: { text: '数据不足', color: '#8c8c8c', icon: <QuestionOutlined /> },
};

const VERDICT_COLOR: Record<string, string> = {
  重点跟踪: '#3d9915',
  中性观察: '#cc8a00',
  谨慎回避: '#d42a2c',
  数据不足: '#8c8c8c',
};

interface StockTearSheetProps {
  stock: Stock;
  onBack: () => void;
  onViewChange: (view: ViewType) => void;
}

const StockTearSheet: React.FC<StockTearSheetProps> = ({ stock, onBack, onViewChange }) => {
  const [sheet, setSheet] = useState<TearSheetResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSheet(await getTearSheet(stock.symbol, stock.name, stock.marketCap, stock.market));
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '速判卡生成失败，请确认后端 API 已启动');
    } finally {
      setLoading(false);
    }
  }, [stock.symbol, stock.name, stock.marketCap, stock.market]);

  useEffect(() => {
    void load();
  }, [load]);

  const verdictColor = sheet ? VERDICT_COLOR[sheet.overall_verdict] || '#8c8c8c' : '#8c8c8c';
  const price = sheet?.price ?? stock.currentPrice;
  const chg = sheet?.change_percent ?? stock.changePercent;
  const up = (chg ?? 0) >= 0;

  const renderDimension = (d: TearSheetDimension) => (
    <Col xs={24} md={12} key={d.key}>
      <DimensionCard dim={d} meta={SIGNAL_META[d.signal]} showConfidence />
    </Col>
  );

  return (
    <CenterShell
      eyebrow="STOCK TEAR SHEET"
      title={(
        <Space>
          <ThunderboltOutlined />
          {stock.name}
        </Space>
      )}
      subtitle={`${stock.symbol} · 机构级证据速判卡`}
      actions={(
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
            返回
          </Button>
          <Button icon={<ExperimentOutlined />} onClick={() => onViewChange('ai-research')}>
            深入体检
          </Button>
          <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={load}>
            刷新
          </Button>
        </Space>
      )}
      error={error}
      loading={loading && !sheet}
      loadingText="正在聚合行情、财报、期权多源证据并逐维度校验…"
      dataQuality={sheet?.data_quality}
    >
      {sheet && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card>
            <Row gutter={[24, 16]} align="middle">
              <Col xs={24} md={6} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: verdictColor }}>{sheet.overall_verdict}</div>
                <Text type="secondary">
                  综合速判 · {sheet.overall_score > 0 ? '+' : ''}
                  {sheet.overall_score}
                </Text>
                <Progress
                  percent={Math.round(sheet.confidence * 100)}
                  size="small"
                  strokeColor={verdictColor}
                  format={(p) => `置信 ${p}%`}
                />
              </Col>
              <Col xs={24} md={18}>
                <Paragraph style={{ fontSize: 15, marginBottom: 8 }}>{sheet.narrative}</Paragraph>
                <Space size={8} wrap style={{ marginBottom: 8 }}>
                  <NarrativeBadge provider={sheet.narrative_provider} />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    数据生成 {formatGeneratedAt(sheet.generated_at)}
                  </Text>
                </Space>
                <Space size={16} wrap>
                  <Text strong style={{ fontSize: 18 }}>
                    {typeof price === 'number' ? price.toFixed(2) : '—'} {sheet.currency}
                  </Text>
                  <Text style={{ color: up ? '#d42a2c' : '#3d9915', fontSize: 15 }}>
                    {typeof chg === 'number' ? `${up ? '+' : ''}${chg.toFixed(2)}%` : ''}
                  </Text>
                </Space>
              </Col>
            </Row>
          </Card>

          <PriceTrend series={sheet.price_series} currency={sheet.currency} />

          <MarketRateTrend
            sp500Series={sheet.sp500_series}
            us10ySeries={sheet.us10y_series}
            title="市场环境趋势 · 真实数据"
            indexName={(sheet.dimensions.find((d) => d.key === 'market')?.label || '').replace('市场环境 · ', '') || '标普500'}
          />

          {DIM_GROUPS.map((g) => {
            const groupDims = sheet.dimensions.filter((d) => g.keys.includes(d.key));
            if (groupDims.length === 0) {
              return null;
            }
            return (
              <div key={g.title}>
                <Text type="secondary" style={{ fontSize: 13, fontWeight: 600, display: 'block', marginBottom: 8 }}>
                  {g.title}
                </Text>
                <Row gutter={[16, 16]}>{groupDims.map(renderDimension)}</Row>
              </div>
            );
          })}

          <Text type="secondary" style={{ fontSize: 12 }}>
            {sheet.disclaimer}
          </Text>
        </Space>
      )}
    </CenterShell>
  );
};

export default StockTearSheet;
