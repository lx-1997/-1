import React, { useState, useEffect } from 'react';
import { Card, Select, Space, Typography, Spin } from 'antd';
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Bar,
  ReferenceLine
} from 'recharts';

const { Option } = Select;
const { Text } = Typography;

interface KlineChartProps {
  symbol: string;
}

const KlineChart: React.FC<KlineChartProps> = ({ symbol }) => {
  const [data, setData] = useState<any[]>([]); // Changed KlineData to any[] as generateKlineData is removed
  const [timeframe, setTimeframe] = useState('1D');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    // 模拟数据加载
    setTimeout(() => {
      // 模拟数据加载
      const klineData = [
        { timestamp: Date.now() - 30 * 60 * 1000, open: 100, high: 105, low: 95, close: 102, volume: 100000 },
        { timestamp: Date.now() - 20 * 60 * 1000, open: 102, high: 108, low: 98, close: 105, volume: 120000 },
        { timestamp: Date.now() - 10 * 60 * 1000, open: 105, high: 110, low: 100, close: 108, volume: 150000 },
        { timestamp: Date.now(), open: 108, high: 112, low: 105, close: 110, volume: 180000 },
      ];
      setData(klineData);
      setLoading(false);
    }, 500);
  }, [symbol, timeframe]);

  const formatTooltipValue = (value: number, name: string) => {
    if (name === 'volume') {
      return [value.toLocaleString(), '成交量'];
    }
    return [`¥${value.toFixed(2)}`, name];
  };

  const formatXAxisLabel = (timestamp: number) => {
    const date = new Date(timestamp);
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  };

  if (loading) {
    return (
      <div style={{ height: '400px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      {/* 时间周期选择器 */}
      <div style={{ marginBottom: '16px', textAlign: 'right' }}>
        <Space>
          <Text>时间周期：</Text>
          <Select
            value={timeframe}
            onChange={setTimeframe}
            style={{ width: 100 }}
            size="small"
          >
            <Option value="1D">1日</Option>
            <Option value="1W">1周</Option>
            <Option value="1M">1月</Option>
            <Option value="3M">3月</Option>
            <Option value="6M">6月</Option>
            <Option value="1Y">1年</Option>
          </Select>
        </Space>
      </div>

      {/* K线图 */}
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatXAxisLabel}
            stroke="#666"
            fontSize={12}
          />
          <YAxis
            yAxisId="price"
            orientation="right"
            stroke="#666"
            fontSize={12}
            tickFormatter={(value) => `¥${value}`}
          />
          <YAxis
            yAxisId="volume"
            orientation="left"
            stroke="#666"
            fontSize={12}
            tickFormatter={(value) => `${(value / 1000000).toFixed(1)}M`}
          />
          <Tooltip
            formatter={formatTooltipValue}
            labelFormatter={(timestamp) => {
              const date = new Date(timestamp);
              return date.toLocaleString('zh-CN');
            }}
            contentStyle={{
              backgroundColor: '#fff',
              border: '1px solid #d9d9d9',
              borderRadius: '6px'
            }}
          />
          
          {/* 成交量柱状图 */}
          <Bar
            yAxisId="volume"
            dataKey="volume"
            fill="#1890ff"
            opacity={0.3}
            name="成交量"
          />
          
          {/* 价格线 */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="close"
            stroke="#1890ff"
            strokeWidth={2}
            dot={false}
            name="收盘价"
          />
          
          {/* 开盘价线 */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="open"
            stroke="#52c41a"
            strokeWidth={1}
            dot={false}
            strokeDasharray="5 5"
            name="开盘价"
          />
          
          {/* 最高价线 */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="high"
            stroke="#f5222d"
            strokeWidth={1}
            dot={false}
            strokeDasharray="3 3"
            name="最高价"
          />
          
          {/* 最低价线 */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="low"
            stroke="#fa8c16"
            strokeWidth={1}
            dot={false}
            strokeDasharray="3 3"
            name="最低价"
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* 技术指标 */}
      <div style={{ marginTop: '16px', padding: '12px', background: '#fafafa', borderRadius: '6px' }}>
        <Text strong style={{ fontSize: '14px' }}>技术指标</Text>
        <div style={{ marginTop: '8px', display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
          <div>
            <Text style={{ fontSize: '12px', color: '#666' }}>MA5: </Text>
            <Text style={{ fontSize: '12px', fontWeight: 'bold' }}>
              ¥{data.length > 0 ? data[data.length - 1].close.toFixed(2) : '0.00'}
            </Text>
          </div>
          <div>
            <Text style={{ fontSize: '12px', color: '#666' }}>MA10: </Text>
            <Text style={{ fontSize: '12px', fontWeight: 'bold' }}>
              ¥{data.length > 0 ? (data[data.length - 1].close * 0.98).toFixed(2) : '0.00'}
            </Text>
          </div>
          <div>
            <Text style={{ fontSize: '12px', color: '#666' }}>MA20: </Text>
            <Text style={{ fontSize: '12px', fontWeight: 'bold' }}>
              ¥{data.length > 0 ? (data[data.length - 1].close * 0.95).toFixed(2) : '0.00'}
            </Text>
          </div>
          <div>
            <Text style={{ fontSize: '12px', color: '#666' }}>RSI: </Text>
            <Text style={{ fontSize: '12px', fontWeight: 'bold' }}>
              {(Math.random() * 100).toFixed(1)}
            </Text>
          </div>
        </div>
      </div>
    </div>
  );
};

export default KlineChart;
