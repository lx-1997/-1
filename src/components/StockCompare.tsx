import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, Empty, Row, Space, Table, Tag, Typography } from 'antd';
import { ReloadOutlined, SwapOutlined } from '@ant-design/icons';
import CenterShell from './common/CenterShell';
import DataQualityBanner from './common/DataQualityBanner';
import {
  getStockCompare,
  type StockCompareItem,
  type StockCompareResponse,
  type TearSheetSignal,
} from '../services/researchService';

const { Text } = Typography;

const SIGNAL_DOT: Record<TearSheetSignal, { color: string }> = {
  bullish: { color: '#3d9915' },
  bearish: { color: '#d42a2c' },
  neutral: { color: '#cc8a00' },
  insufficient: { color: '#bfbfbf' },
};

const VERDICT_COLOR: Record<string, string> = {
  重点跟踪: '#3d9915',
  中性观察: '#cc8a00',
  谨慎回避: '#d42a2c',
  数据不足: '#8c8c8c',
};

// 矩阵展示的维度顺序：真实可区分（市场/利率/规模）在前，受 403 限的个股维度在后。
const DIMS: { key: string; label: string }[] = [
  { key: 'market', label: '市场环境' },
  { key: 'macro', label: '宏观利率' },
  { key: 'scale', label: '规模 / 流动性' },
  { key: 'momentum', label: '价格动量' },
  { key: 'options', label: '期权情绪' },
  { key: 'catalyst', label: '盈利催化' },
];

interface StockCompareProps {
  symbols?: string[];
  caps?: (number | undefined)[];
}

const StockCompare: React.FC<StockCompareProps> = ({ symbols, caps }) => {
  const [data, setData] = useState<StockCompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const symbolsKey = (symbols || []).join(',');
  const capsKey = (caps || []).map((c) => (c ?? '')).join(',');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const syms = symbolsKey ? symbolsKey.split(',') : [];
      const cps = capsKey ? capsKey.split(',') : [];
      setData(await getStockCompare(syms, cps));
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '对比加载失败，请确认后端 API 已启动');
    } finally {
      setLoading(false);
    }
  }, [symbolsKey, capsKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const items: StockCompareItem[] = data?.items || [];

  const columns = [
    {
      title: '维度',
      dataIndex: 'label',
      key: 'label',
      fixed: 'left' as const,
      width: 110,
      render: (t: string) => <Text strong>{t}</Text>,
    },
    ...items.map((it) => ({
      title: (
        <Space direction="vertical" size={2} style={{ lineHeight: 1.3 }}>
          <Text strong>{it.symbol}</Text>
          <Tag color={VERDICT_COLOR[it.overall_verdict] || '#8c8c8c'} style={{ marginInlineEnd: 0 }}>
            {it.overall_verdict}
          </Tag>
        </Space>
      ),
      key: it.symbol,
      width: 200,
      render: (_: unknown, row: { key: string }) => {
        const dim = it.dimensions.find((d) => d.key === row.key);
        if (!dim) return <Text type="secondary">—</Text>;
        const dot = SIGNAL_DOT[dim.signal];
        return (
          <span style={{ fontSize: 13 }}>
            <span style={{ color: dot.color, marginRight: 6 }}>●</span>
            {dim.signal === 'insufficient' ? <Text type="secondary">数据不足</Text> : dim.headline}
          </span>
        );
      },
    })),
  ];

  const dataSource = DIMS.map((d) => ({ key: d.key, label: d.label }));

  return (
    <CenterShell
      eyebrow="STOCK COMPARE"
      title={(
        <Space>
          <SwapOutlined />
          多标的横向对比
        </Space>
      )}
      subtitle="买方候选取舍 · 逐维度信号灯矩阵"
      actions={(
        <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      )}
      error={error}
      loading={loading && !data}
      loadingText="正在并排聚合各标的速判维度…"
      dataQuality={data?.data_quality}
    >
      {data && items.length === 0 && (
        <Empty description="没有可对比的标的——请先在观察池添加标的" />
      )}
      {data && items.length > 0 && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            {items.map((it) => (
              <Col key={it.symbol} xs={12} sm={8} md={6}>
                <Card size="small" style={{ borderTop: `3px solid ${VERDICT_COLOR[it.overall_verdict] || '#8c8c8c'}` }}>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{it.symbol}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.7 }}>
                    {it.sector || '非标普500 成分'}
                    <br />
                    {typeof it.market_cap === 'number' ? `市值 ${(it.market_cap / 1e8).toLocaleString()} 亿` : '市值未知'}
                  </div>
                </Card>
              </Col>
            ))}
          </Row>

          <Table
            columns={columns}
            dataSource={dataSource}
            pagination={false}
            size="small"
            scroll={{ x: 'max-content' }}
            bordered
          />

          <DataQualityBanner quality={data.data_quality} />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {data.disclaimer}
          </Text>
        </Space>
      )}
    </CenterShell>
  );
};

export default StockCompare;
