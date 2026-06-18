import React from 'react';
import { Card, Space, Tag } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export type SeriesPoint = { date: string; value: number };

/** 把 S&P500 与美10Y 两条 series 按日期对齐成一张双轴折线图的数据。 */
function mergeSeries(sp?: SeriesPoint[], rate?: SeriesPoint[]) {
  const map = new Map<string, { date: string; sp500?: number; us10y?: number }>();
  (sp || []).forEach((p) => map.set(p.date, { ...(map.get(p.date) || { date: p.date }), sp500: p.value }));
  (rate || []).forEach((p) => map.set(p.date, { ...(map.get(p.date) || { date: p.date }), us10y: p.value }));
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
}

interface MarketRateTrendProps {
  sp500Series?: SeriesPoint[];
  us10ySeries?: SeriesPoint[];
  title?: string;
  /** 大盘指数名（美股=标普500、A股=沪深300、港股=恒生指数），用于图例与标签。 */
  indexName?: string;
}

/**
 * 共享的「市场 + 利率」双轴真实数据趋势图（github · S&P500 / 10Y）。
 * 个股速览 / 组合速判 / 宏观速判三处复用，无数据时返回 null。
 */
const MarketRateTrend: React.FC<MarketRateTrendProps> = ({
  sp500Series,
  us10ySeries,
  title = '市场与利率趋势 · 真实数据',
  indexName = '标普500',
}) => {
  if (!sp500Series?.length && !us10ySeries?.length) return null;
  return (
    <Card
      size="small"
      title={(
        <Space>
          <LineChartOutlined />
          {title}
        </Space>
      )}
      extra={<Tag color="#3d9915">{indexName} / 美10Y · 真实</Tag>}
    >
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={mergeSeries(sp500Series, us10ySeries)} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" tickFormatter={(d) => (d || '').slice(0, 7)} fontSize={11} stroke="var(--text-muted)" />
          <YAxis yAxisId="sp" orientation="left" domain={['auto', 'auto']} fontSize={11} width={50} stroke="#1769aa" />
          <YAxis yAxisId="rate" orientation="right" domain={['auto', 'auto']} fontSize={11} width={42} stroke="#cc8a00" tickFormatter={(v) => `${v}%`} />
          <Tooltip />
          <Line yAxisId="sp" type="monotone" dataKey="sp500" name={indexName} stroke="#1769aa" strokeWidth={2} dot={false} connectNulls />
          <Line yAxisId="rate" type="monotone" dataKey="us10y" name="美10Y%" stroke="#cc8a00" strokeWidth={2} strokeDasharray="4 2" dot={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
};

export default MarketRateTrend;
