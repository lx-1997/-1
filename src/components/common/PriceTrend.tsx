import React from 'react';
import { Card, Space, Tag } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

interface PriceTrendProps {
  series?: { date: string; value: number }[];
  currency?: string;
  title?: string;
}

/**
 * 个股价格走势图（单轴面积图，Yahoo Finance 真实日线收盘）。
 * 红涨绿跌（A股习惯）；无数据返回 null。
 */
const PriceTrend: React.FC<PriceTrendProps> = ({ series, currency = 'USD', title = '价格走势 · 近 6 月' }) => {
  if (!series?.length) return null;
  const first = series[0].value;
  const last = series[series.length - 1].value;
  const up = last >= first;
  const color = up ? '#d42a2c' : '#3d9915';
  return (
    <Card
      size="small"
      title={(
        <Space>
          <LineChartOutlined />
          {title}
        </Space>
      )}
      extra={<Tag color="#1769aa">Yahoo Finance · 真实日线</Tag>}
    >
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="dfxPriceGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="date"
            tickFormatter={(d) => (d || '').slice(5)}
            fontSize={11}
            stroke="var(--text-muted)"
            minTickGap={40}
          />
          <YAxis domain={['auto', 'auto']} fontSize={11} width={52} stroke="var(--text-muted)" tickFormatter={(v) => `${v}`} />
          <Tooltip formatter={(v: number) => [`${v} ${currency}`, '收盘']} />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2} fill="url(#dfxPriceGrad)" dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
};

export default PriceTrend;
