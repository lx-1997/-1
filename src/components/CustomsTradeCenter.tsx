import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography
} from 'antd';
import {
  CloudDownloadOutlined,
  DatabaseOutlined,
  GlobalOutlined,
  LinkOutlined,
  ReloadOutlined,
  RiseOutlined,
  RobotOutlined,
  SearchOutlined,
  SwapOutlined
} from '@ant-design/icons';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from 'recharts';
import { AppState } from '../types';
import {
  CustomsHsSectionRow,
  CustomsHsDetailPartner,
  CustomsHsDetailPoint,
  CustomsHsDetailSnapshot,
  CustomsMajorExportRow,
  CustomsPartnerRow,
  CustomsTradeSnapshot,
  CustomsTotalRow,
  analyzeCustomsTrade,
  fetchCustomsHsDetail,
  fetchCustomsTradeSnapshot
} from '../services/specializedService';
import type { FinGptTaskResponse } from '../services/researchService';
import CenterShell from './common/CenterShell';
import ShareButton from './common/ShareButton';
import './CustomsTradeCenter.css';

const { Paragraph, Text } = Typography;

interface CustomsTradeCenterProps {
  appState: AppState;
}

type DetailTab = 'fine' | 'chapters' | 'exports' | 'partners' | 'hs';

const totalLabel: Record<string, string> = {
  total: '进出口总值',
  export: '出口',
  import: '进口',
  balance: '贸易差额'
};

const CustomsTradeCenter: React.FC<CustomsTradeCenterProps> = () => {
  const { message } = AntdApp.useApp();
  const [snapshot, setSnapshot] = useState<CustomsTradeSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiAnalysis, setAiAnalysis] = useState<FinGptTaskResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>('chapters');
  const [commodityQuery, setCommodityQuery] = useState('');
  const [hsDetailQuery, setHsDetailQuery] = useState('硝化棉');
  const [hsDetail, setHsDetail] = useState<CustomsHsDetailSnapshot | null>(null);
  const [hsDetailLoading, setHsDetailLoading] = useState(false);
  const [hsDetailError, setHsDetailError] = useState<string | null>(null);
  const [selectedHsTrendKey, setSelectedHsTrendKey] = useState<string | null>(null);
  const [selectedCommodityTrendKey, setSelectedCommodityTrendKey] = useState<string | null>(null);

  const loadSnapshot = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCustomsTradeSnapshot();
      setSnapshot(data);
      setAiAnalysis(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '海关进出口数据读取失败');
    } finally {
      setLoading(false);
    }
  };

  const loadHsDetail = async (nextQuery = hsDetailQuery, nextCode?: string | null) => {
    const trimmedQuery = nextQuery.trim();
    if (!trimmedQuery && !nextCode) {
      message.warning('请输入商品名或HS编码');
      return;
    }
    setHsDetailLoading(true);
    setHsDetailError(null);
    try {
      const data = await fetchCustomsHsDetail({
        query: trimmedQuery || undefined,
        code: nextCode || undefined,
        months: 12
      });
      setHsDetail(data);
      if (data.product?.name_zh || data.product?.name) {
        setHsDetailQuery(data.product.name_zh || data.product.name || trimmedQuery);
      }
    } catch (err) {
      setHsDetailError(err instanceof Error ? err.message : 'HS明细商品读取失败');
    } finally {
      setHsDetailLoading(false);
    }
  };

  useEffect(() => {
    loadSnapshot();
  }, []);

  useEffect(() => {
    if (detailTab === 'fine' && !hsDetail && !hsDetailLoading) {
      loadHsDetail();
    }
  }, [detailTab, hsDetail, hsDetailLoading]);

  useEffect(() => {
    if (!snapshot) {
      return;
    }
    const defaultHs = snapshot.hs_chapters.find(row => row.code === '85') || snapshot.hs_chapters[0] || snapshot.hs_sections[0];
    const nextHsKey = defaultHs ? hsTrendKey(defaultHs) : null;
    if (nextHsKey && (!selectedHsTrendKey || !snapshot.hs_trends?.[selectedHsTrendKey])) {
      setSelectedHsTrendKey(nextHsKey);
    }

    const commodities = [...(snapshot.major_exports || []), ...(snapshot.major_imports || [])];
    const defaultCommodity = (
      commodities.find(row => (row.commodity_zh || '').includes('集成电路'))
      || commodities.find(row => (row.commodity_zh || '').includes('机电'))
      || commodities[0]
    );
    const nextCommodityKey = defaultCommodity ? commodityTrendKey(defaultCommodity) : null;
    if (nextCommodityKey && (!selectedCommodityTrendKey || !snapshot.commodity_trends?.[selectedCommodityTrendKey])) {
      setSelectedCommodityTrendKey(nextCommodityKey);
    }
  }, [snapshot, selectedHsTrendKey, selectedCommodityTrendKey]);

  const totalRows = snapshot?.total?.items || {};
  const chartRows = useMemo(() => (
    (snapshot?.monthly_trend || []).slice(-12).map(point => ({
      month: point.month.slice(2).replace('-', '/'),
      export: toUsdBn(point.export_usd_mn),
      import: toUsdBn(point.import_usd_mn),
      balance: toUsdBn(point.balance_usd_mn)
    }))
  ), [snapshot]);

  const partnerChartRows = useMemo(() => (
    (snapshot?.partners || [])
      .filter(row => !row.is_region_header)
      .slice(0, 10)
      .map(row => ({
        name: compactName(row.name_zh || row.name),
        export: toUsdBn(row.ytd_export_usd_mn),
        import: toUsdBn(row.ytd_import_usd_mn)
      }))
      .reverse()
  ), [snapshot]);

  const filteredHsChapters = useMemo(() => {
    const query = commodityQuery.trim().toLowerCase();
    const rows = snapshot?.hs_chapters || [];
    if (!query) {
      return rows;
    }
    return rows.filter(row => (
      row.name.toLowerCase().includes(query)
      || (row.code || '').toLowerCase().includes(query)
      || (row.description || '').toLowerCase().includes(query)
      || (row.description_zh || '').toLowerCase().includes(query)
      || (row.name_zh || '').toLowerCase().includes(query)
    ));
  }, [commodityQuery, snapshot]);

  const majorCommodityRows = useMemo(() => (
    [...(snapshot?.major_exports || []), ...(snapshot?.major_imports || [])]
  ), [snapshot]);

  const filteredMajorExports = useMemo(() => {
    const query = commodityQuery.trim().toLowerCase();
    const rows = majorCommodityRows;
    if (!query) {
      return rows;
    }
    return rows.filter(row => (
      row.commodity.toLowerCase().includes(query)
      || (row.commodity_zh || '').toLowerCase().includes(query)
      || directionLabel(row.direction).toLowerCase().includes(query)
    ));
  }, [commodityQuery, majorCommodityRows]);

  const selectedHsRow = useMemo(() => {
    const rows = [...(snapshot?.hs_chapters || []), ...(snapshot?.hs_sections || [])];
    return rows.find(row => hsTrendKey(row) === selectedHsTrendKey);
  }, [snapshot, selectedHsTrendKey]);

  const selectedCommodityRow = useMemo(() => (
    majorCommodityRows.find(row => commodityTrendKey(row) === selectedCommodityTrendKey)
  ), [majorCommodityRows, selectedCommodityTrendKey]);

  const trendChartRows = useMemo(() => {
    if (!snapshot || detailTab === 'partners') {
      return [];
    }
    if (detailTab === 'fine') {
      return (hsDetail?.monthly_points || []).map(point => ({
        month: point.month.slice(2).replace('-', '/'),
        trade: toUsdBnRaw(point.trade_value_usd),
        export: toUsdBnRaw(point.export_value_usd),
        import: toUsdBnRaw(point.import_value_usd)
      }));
    }
    if (detailTab === 'exports') {
      return (snapshot.commodity_trends?.[selectedCommodityTrendKey || ''] || []).map(point => ({
        month: point.month.slice(2).replace('-', '/'),
        value: toUsdBn(point.value_usd_mn),
        quantity: point.quantity
      }));
    }
    return (snapshot.hs_trends?.[selectedHsTrendKey || ''] || []).map(point => ({
      month: point.month.slice(2).replace('-', '/'),
      trade: toUsdBn(point.trade_usd_mn),
      export: toUsdBn(point.export_usd_mn),
      import: toUsdBn(point.import_usd_mn)
    }));
  }, [detailTab, hsDetail, selectedCommodityTrendKey, selectedHsTrendKey, snapshot]);

  const trendTitle = detailTab === 'fine'
    ? `${hsDetail?.product ? `${hsDetail.product.code} · ${hsDetail.product.name_zh || hsDetail.product.name}` : 'HS明细商品'} · 最近12个月`
    : detailTab === 'exports'
    ? `${selectedCommodityRow ? `${directionLabel(selectedCommodityRow.direction)} · ${selectedCommodityRow.commodity_zh || selectedCommodityRow.commodity}` : '重点商品'} · 最近12个月`
    : `${selectedHsRow ? displayHsName(selectedHsRow) : 'HS商品'} · 最近12个月`;

  const selectedFocusLabel = useMemo(() => {
    if (detailTab === 'exports' && selectedCommodityRow) {
      return `${directionLabel(selectedCommodityRow.direction)}重点商品：${selectedCommodityRow.commodity_zh || selectedCommodityRow.commodity}`;
    }
    if (detailTab === 'chapters' && selectedHsRow) {
      return `HS2商品：${selectedHsRow.code || ''} ${displayHsName(selectedHsRow)}`.trim();
    }
    if (detailTab === 'hs' && selectedHsRow) {
      return `HS大类：${selectedHsRow.name_zh || cleanHsName(selectedHsRow.name)}`;
    }
    if (detailTab === 'fine' && hsDetail?.product) {
      return `HS6明细：${hsDetail.product.code} ${hsDetail.product.name_zh || hsDetail.product.name || hsDetailQuery}`;
    }
    if (detailTab === 'partners') {
      return '贸易伙伴结构';
    }
    return '全局海关进出口快照';
  }, [detailTab, hsDetail, hsDetailQuery, selectedCommodityRow, selectedHsRow]);

  const handleAiAnalysis = async () => {
    if (!snapshot) {
      message.warning('请先加载海关进出口数据');
      return;
    }
    setAiLoading(true);
    try {
      const data = await analyzeCustomsTrade({
        focus: selectedFocusLabel,
        focus_key: detailTab === 'fine' ? hsDetail?.product?.code || hsDetail?.code : detailTab === 'exports' ? selectedCommodityTrendKey : selectedHsTrendKey,
        focus_type: detailTab === 'fine' ? 'hs_detail' : detailTab === 'exports' ? 'commodity' : detailTab === 'partners' ? 'partner' : 'hs',
        selected_tab: detailTab
      });
      setAiAnalysis(data);
      message.success('海关进出口AI分析已生成');
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '请确认后端 API 已启动，且模型配置可用';
      message.error(`AI分析失败：${detail}`);
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <CenterShell
      className="customs-trade-shell"
      icon={<GlobalOutlined className="customs-trade-title-icon" />}
      title={(
        <>
          中国海关进出口{' '}
          <Tag color={!snapshot ? 'blue' : snapshot.source_status === 'live' ? 'green' : 'gold'}>
            {!snapshot ? '加载中' : snapshot.source_status === 'live' ? '官方实时抓取' : '部分数据'}
          </Tag>
        </>
      )}
      subtitle={`${snapshot?.month_label || '最新月份'} · 海关总署英文站 · 单位统一折算为 USD million`}
      actions={(
        <Space wrap className="customs-trade-actions">
          {snapshot?.total?.source_url && (
            <Button
              icon={<LinkOutlined />}
              href={snapshot.total.source_url}
              target="_blank"
            >
              总量源
            </Button>
          )}
          {snapshot?.total?.download_url && (
            <Button
              icon={<CloudDownloadOutlined />}
              href={snapshot.total.download_url}
              target="_blank"
            >
              Excel
            </Button>
          )}
          <Button
            type="primary"
            icon={<RobotOutlined />}
            loading={aiLoading}
            disabled={!snapshot || loading}
            onClick={handleAiAnalysis}
          >
            一键AI分析
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={loadSnapshot}
          >
            刷新
          </Button>
        </Space>
      )}
    >

      {error && (
        <Alert
          className="customs-trade-alert"
          type="error"
          showIcon
          message="读取失败"
          description={error}
        />
      )}

      {!!snapshot?.warnings?.length && (
        <Alert
          className="customs-trade-alert"
          type="warning"
          showIcon
          message="数据源提示"
          description={snapshot.warnings.join('；')}
        />
      )}

      <Row gutter={[12, 12]} className="customs-trade-kpis">
        <Col xs={24} sm={12} xl={6}>
          <MetricCard icon={<SwapOutlined />} row={totalRows.total} label="当月进出口" />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard icon={<RiseOutlined />} row={totalRows.export} label="累计出口" preferYtd />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard icon={<DatabaseOutlined />} row={totalRows.import} label="累计进口" preferYtd />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard icon={<SwapOutlined />} row={totalRows.balance} label="当月顺差" />
        </Col>
      </Row>

      {aiAnalysis && (
        <Card
          className="customs-trade-ai-card"
          title={<Space><RobotOutlined />投研Agent解读</Space>}
          extra={
            <Space wrap>
              <ShareButton
                modalTitle="分享海关贸易分析"
                target={() => ({
                  title: '中国海关进出口 AI 分析',
                  summary: aiAnalysis.summary,
                  byline: '由 DeepFocus 海关贸易分析生成',
                })}
              />
              <Tag color="blue">置信 {formatConfidence(aiAnalysis.confidence)}</Tag>
              <Text type="secondary">{aiAnalysis.provider} · {aiAnalysis.model}</Text>
            </Space>
          }
        >
          <Paragraph className="customs-trade-ai-summary">{aiAnalysis.summary}</Paragraph>
          <div className="customs-trade-ai-grid">
            <AnalysisList title="核心结论" items={aiAnalysis.key_points} />
            <AnalysisList title="可投资线索" items={aiAnalysis.signals} />
            <AnalysisList title="风险提示" items={aiAnalysis.risks} />
            <AnalysisList title="投资建议与代表股票" items={aiAnalysis.actions} />
          </div>
          <Alert
            className="customs-trade-ai-note"
            type="info"
            showIcon
            message="Agent已读取近12个月趋势，并给出非个性化投研建议与代表标的池"
            description={aiAnalysis.disclaimer}
          />
        </Card>
      )}

      <div className="customs-trade-grid">
        <Card title="最近12个月总量走势" loading={loading && !snapshot}>
          {chartRows.length ? (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartRows} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6eaf0" />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={value => `${value}B`} />
                <RechartsTooltip formatter={(value: number) => [`$${value.toFixed(1)}B`, '']} />
                <Legend />
                <Line type="monotone" dataKey="export" name="出口" stroke="#0f766e" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="import" name="进口" stroke="#2563eb" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="balance" name="差额" stroke="#b45309" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无月度数据" />
          )}
        </Card>

        <Card title="主要伙伴累计结构" loading={loading && !snapshot}>
          {partnerChartRows.length ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart
                data={partnerChartRows}
                layout="vertical"
                margin={{ top: 6, right: 18, left: 20, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e6eaf0" />
                <XAxis type="number" tick={{ fontSize: 12 }} tickFormatter={value => `${value}B`} />
                <YAxis dataKey="name" type="category" tick={{ fontSize: 12 }} width={86} />
                <RechartsTooltip formatter={(value: number) => [`$${value.toFixed(1)}B`, '']} />
                <Legend />
                <Bar dataKey="export" name="累计出口" fill="#0f766e" radius={[0, 3, 3, 0]} />
                <Bar dataKey="import" name="累计进口" fill="#2563eb" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无伙伴数据" />
          )}
        </Card>
      </div>

      {detailTab !== 'partners' && (
        <Card className="customs-trade-trend-card" title={trendTitle} loading={(loading && !snapshot) || (detailTab === 'fine' && hsDetailLoading)}>
          {trendChartRows.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trendChartRows} margin={{ top: 8, right: 18, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6eaf0" />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={value => `${value}B`} />
                <RechartsTooltip
                  formatter={(value: number, name: string) => [
                    name === 'quantity' ? formatQuantity(value) : detailTab === 'fine' ? `$${value.toFixed(3)}B` : `$${value.toFixed(1)}B`,
                    trendLineName(name)
                  ]}
                />
                <Legend />
                {detailTab === 'exports' ? (
                  <Line type="monotone" dataKey="value" name="金额" stroke="#0f766e" strokeWidth={2} dot={false} />
                ) : (
                  <>
                    <Line type="monotone" dataKey="trade" name="贸易额" stroke="#475569" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="export" name="出口" stroke="#0f766e" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="import" name="进口" stroke="#2563eb" strokeWidth={2} dot={false} />
                  </>
                )}
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无12个月数据" />
          )}
        </Card>
      )}

      <Card
        className="customs-trade-detail"
        title="结构明细"
        extra={
          <Space wrap className="customs-trade-detail-tools">
            {(detailTab === 'chapters' || detailTab === 'exports') && (
              <Input
                allowClear
                size="small"
                placeholder="搜索 HS编码/商品"
                value={commodityQuery}
                onChange={event => setCommodityQuery(event.target.value)}
                className="customs-trade-search"
              />
            )}
            {detailTab === 'fine' && (
              <Input.Search
                allowClear
                size="small"
                enterButton={<SearchOutlined />}
                placeholder="硝化棉 / 391220 / 变压器"
                value={hsDetailQuery}
                loading={hsDetailLoading}
                onChange={event => setHsDetailQuery(event.target.value)}
                onSearch={() => loadHsDetail()}
                className="customs-trade-search"
              />
            )}
            <Segmented
              size="small"
              className="customs-trade-detail-tabs"
              value={detailTab}
              onChange={value => setDetailTab(value as DetailTab)}
              options={[
                { label: 'HS明细', value: 'fine' },
                { label: 'HS2商品', value: 'chapters' },
                { label: '重点商品', value: 'exports' },
                { label: '伙伴', value: 'partners' },
                { label: 'HS大类', value: 'hs' }
              ]}
            />
          </Space>
        }
      >
        {detailTab === 'fine' && (
          <div className="customs-trade-hs-detail">
            {hsDetailError && (
              <Alert
                type="error"
                showIcon
                message="HS明细读取失败"
                description={hsDetailError}
                className="customs-trade-alert"
              />
            )}
            {!!hsDetail?.warnings?.length && (
              <Alert
                type="info"
                showIcon
                message="细商品数据口径"
                description={hsDetail.warnings.join('；')}
                className="customs-trade-alert"
              />
            )}
            {hsDetail?.product && (
              <div className="customs-trade-hs-detail-head">
                <Space wrap size={8}>
                  <Tag color="blue">HS6 {hsDetail.product.code}</Tag>
                  <Text strong>{hsDetail.product.name_zh || hsDetail.product.name}</Text>
                  {hsDetail.product.industry && <Tag color="green">{hsDetail.product.industry}</Tag>}
                  <Text type="secondary">最新可用：{hsDetail.month_label || '—'}</Text>
                  <Text type="secondary">来源：UN Comtrade 中国报告方</Text>
                </Space>
                {!!hsDetail.candidates?.length && (
                  <Space wrap size={6}>
                    {hsDetail.candidates.slice(0, 6).map(candidate => (
                      <Button
                        key={candidate.code}
                        size="small"
                        type={candidate.code === hsDetail.product?.code ? 'primary' : 'default'}
                        onClick={() => loadHsDetail(candidate.name_zh || candidate.name || candidate.code, candidate.code)}
                      >
                        {candidate.code} {candidate.name_zh || candidate.name}
                      </Button>
                    ))}
                  </Space>
                )}
              </div>
            )}
            {!hsDetail?.product && !!hsDetail?.candidates?.length && (
              <Space wrap size={6} className="customs-trade-hs-candidates">
                {hsDetail.candidates.slice(0, 8).map(candidate => (
                  <Button
                    key={candidate.code}
                    size="small"
                    onClick={() => loadHsDetail(candidate.name_zh || candidate.name || candidate.code, candidate.code)}
                  >
                    {candidate.code} {candidate.name_zh || candidate.name}
                  </Button>
                ))}
              </Space>
            )}
            <Table<CustomsHsDetailPoint>
              rowKey={row => row.period}
              size="small"
              loading={hsDetailLoading}
              dataSource={hsDetail?.monthly_points || []}
              pagination={false}
              tableLayout="fixed"
              scroll={{ x: 1240 }}
              columns={[
                { title: '月份', dataIndex: 'month', width: 84, sorter: (a, b) => compareText(a.month, b.month) },
                { title: '贸易额', dataIndex: 'trade_value_usd', width: 112, align: 'right', sorter: (a, b) => compareNumber(a.trade_value_usd, b.trade_value_usd), render: value => formatUsdRaw(value) },
                { title: '出口金额', dataIndex: 'export_value_usd', width: 112, align: 'right', sorter: (a, b) => compareNumber(a.export_value_usd, b.export_value_usd), render: value => formatUsdRaw(value) },
                {
                  title: '出口数量',
                  dataIndex: 'export_quantity',
                  width: 132,
                  align: 'right',
                  sorter: (a, b) => compareNumber(a.export_quantity, b.export_quantity),
                  render: (value, row) => formatQuantityWithUnit(value, row.export_quantity_unit)
                },
                { title: '进口金额', dataIndex: 'import_value_usd', width: 112, align: 'right', sorter: (a, b) => compareNumber(a.import_value_usd, b.import_value_usd), render: value => formatUsdRaw(value) },
                {
                  title: '进口数量',
                  dataIndex: 'import_quantity',
                  width: 132,
                  align: 'right',
                  sorter: (a, b) => compareNumber(a.import_quantity, b.import_quantity),
                  render: (value, row) => formatQuantityWithUnit(value, row.import_quantity_unit)
                },
                { title: '差额', dataIndex: 'balance_value_usd', width: 112, align: 'right', sorter: (a, b) => compareNumber(a.balance_value_usd, b.balance_value_usd), render: value => formatUsdRaw(value, true) },
                { title: '贸易环比', dataIndex: 'trade_mom_pct', width: 96, align: 'right', sorter: (a, b) => compareNumber(a.trade_mom_pct, b.trade_mom_pct), render: formatPctTag },
                { title: '出口环比', dataIndex: 'export_mom_pct', width: 96, align: 'right', sorter: (a, b) => compareNumber(a.export_mom_pct, b.export_mom_pct), render: formatPctTag },
                { title: '进口环比', dataIndex: 'import_mom_pct', width: 96, align: 'right', sorter: (a, b) => compareNumber(a.import_mom_pct, b.import_mom_pct), render: formatPctTag },
                { title: '出口单价', dataIndex: 'export_unit_value_usd', width: 104, align: 'right', sorter: (a, b) => compareNumber(a.export_unit_value_usd, b.export_unit_value_usd), render: value => formatUnitValue(value) },
                { title: '进口单价', dataIndex: 'import_unit_value_usd', align: 'right', sorter: (a, b) => compareNumber(a.import_unit_value_usd, b.import_unit_value_usd), render: value => formatUnitValue(value) }
              ]}
            />
            <div className="customs-trade-partner-split">
              <HsDetailPartnerTable title="最新月出口目的地" rows={hsDetail?.top_export_partners || []} loading={hsDetailLoading} />
              <HsDetailPartnerTable title="最新月进口来源地" rows={hsDetail?.top_import_partners || []} loading={hsDetailLoading} />
            </div>
          </div>
        )}

        {detailTab === 'chapters' && (
          <Table<CustomsHsSectionRow>
            rowKey={row => row.code || row.name}
            size="small"
            loading={loading && !snapshot}
            dataSource={filteredHsChapters}
            pagination={{ pageSize: 12, showSizeChanger: false }}
            tableLayout="fixed"
            scroll={{ x: 1080 }}
            rowClassName={row => `customs-trade-clickable-row ${hsTrendKey(row) === selectedHsTrendKey ? 'customs-trade-selected-row' : ''}`}
            onRow={row => ({
              onClick: () => setSelectedHsTrendKey(hsTrendKey(row))
            })}
	            columns={[
	              {
	                title: 'HS',
	                dataIndex: 'code',
	                width: 72,
	                sorter: (a, b) => compareText(a.code || '', b.code || ''),
	                render: value => value ? <Tag color="blue">{value}</Tag> : '—'
	              },
	              {
	                title: '货物章',
	                dataIndex: 'description',
	                width: 220,
	                sorter: (a, b) => compareText(displayHsName(a), displayHsName(b)),
	                render: (value, row) => (
	                  <Tooltip title={row.name}>
	                    <Text strong ellipsis className="customs-trade-commodity-name">
	                      {displayHsName(row)}
	                    </Text>
	                  </Tooltip>
	                )
	              },
	              { title: '累计贸易', dataIndex: 'ytd_trade_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_trade_usd_mn, b.ytd_trade_usd_mn), render: value => formatUsd(value) },
	              { title: '累计出口', dataIndex: 'ytd_export_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_export_usd_mn, b.ytd_export_usd_mn), render: value => formatUsd(value) },
	              { title: '累计进口', dataIndex: 'ytd_import_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_import_usd_mn, b.ytd_import_usd_mn), render: value => formatUsd(value) },
	              { title: '差额', dataIndex: 'ytd_balance_usd_mn', width: 94, align: 'right', sorter: (a, b) => compareNumber(a.ytd_balance_usd_mn, b.ytd_balance_usd_mn), render: value => formatUsd(value, true) },
	              { title: '贸易环比', dataIndex: 'mom_trade_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_trade_pct, b.mom_trade_pct), render: formatPctTag },
	              { title: '出口环比', dataIndex: 'mom_export_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_export_pct, b.mom_export_pct), render: formatPctTag },
	              { title: '进口环比', dataIndex: 'mom_import_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_import_pct, b.mom_import_pct), render: formatPctTag },
	              { title: '出口同比', dataIndex: 'yoy_export_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.yoy_export_pct, b.yoy_export_pct), render: formatPctTag },
	              { title: '进口同比', dataIndex: 'yoy_import_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.yoy_import_pct, b.yoy_import_pct), render: formatPctTag }
	            ]}
	          />
        )}

        {detailTab === 'partners' && (
          <Table<CustomsPartnerRow>
            rowKey={row => row.name}
            size="small"
            loading={loading && !snapshot}
            dataSource={snapshot?.partners || []}
            pagination={{ pageSize: 12, showSizeChanger: false }}
            tableLayout="fixed"
            scroll={{ x: 960 }}
	            columns={[
	              {
	                title: '伙伴/地区',
	                dataIndex: 'name',
	                width: 190,
	                sorter: (a, b) => compareText(a.name_zh || a.name, b.name_zh || b.name),
	                render: (value, row) => (
	                  <Space size={6}>
	                    <Tooltip title={row.name_zh ? value : undefined}>
	                      <Text strong>{row.name_zh || value}</Text>
	                    </Tooltip>
	                    {row.is_region_header && <Tag color="blue">区域</Tag>}
	                  </Space>
	                )
	              },
	              { title: '累计总额', dataIndex: 'ytd_total_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_total_usd_mn, b.ytd_total_usd_mn), render: value => formatUsd(value) },
	              { title: '累计出口', dataIndex: 'ytd_export_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_export_usd_mn, b.ytd_export_usd_mn), render: value => formatUsd(value) },
	              { title: '累计进口', dataIndex: 'ytd_import_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_import_usd_mn, b.ytd_import_usd_mn), render: value => formatUsd(value) },
	              { title: '差额', dataIndex: 'ytd_balance_usd_mn', width: 94, align: 'right', sorter: (a, b) => compareNumber(a.ytd_balance_usd_mn, b.ytd_balance_usd_mn), render: value => formatUsd(value, true) },
	              { title: '总额环比', dataIndex: 'mom_total_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_total_pct, b.mom_total_pct), render: formatPctTag },
	              { title: '出口环比', dataIndex: 'mom_export_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_export_pct, b.mom_export_pct), render: formatPctTag },
	              { title: '进口环比', dataIndex: 'mom_import_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_import_pct, b.mom_import_pct), render: formatPctTag },
	              { title: '总额同比', dataIndex: 'yoy_total_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.yoy_total_pct, b.yoy_total_pct), render: formatPctTag }
	            ]}
	          />
        )}

        {detailTab === 'hs' && (
          <Table<CustomsHsSectionRow>
            rowKey={row => row.name}
            size="small"
            loading={loading && !snapshot}
            dataSource={snapshot?.hs_sections || []}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            tableLayout="fixed"
            scroll={{ x: 980 }}
            rowClassName={row => `customs-trade-clickable-row ${hsTrendKey(row) === selectedHsTrendKey ? 'customs-trade-selected-row' : ''}`}
            onRow={row => ({
              onClick: () => setSelectedHsTrendKey(hsTrendKey(row))
            })}
	            columns={[
	              {
	                title: 'HS大类',
	                dataIndex: 'name',
	                width: 220,
	                sorter: (a, b) => compareText(a.name_zh || cleanHsName(a.name), b.name_zh || cleanHsName(b.name)),
	                render: (value, row) => (
	                  <Tooltip title={value}>
	                    <Text strong ellipsis className="customs-trade-hs-name">
	                      {row.name_zh || cleanHsName(value)}
	                    </Text>
	                  </Tooltip>
	                )
	              },
	              { title: '累计贸易', dataIndex: 'ytd_trade_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_trade_usd_mn, b.ytd_trade_usd_mn), render: value => formatUsd(value) },
	              { title: '累计出口', dataIndex: 'ytd_export_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_export_usd_mn, b.ytd_export_usd_mn), render: value => formatUsd(value) },
	              { title: '累计进口', dataIndex: 'ytd_import_usd_mn', width: 100, align: 'right', sorter: (a, b) => compareNumber(a.ytd_import_usd_mn, b.ytd_import_usd_mn), render: value => formatUsd(value) },
	              { title: '贸易环比', dataIndex: 'mom_trade_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_trade_pct, b.mom_trade_pct), render: formatPctTag },
	              { title: '出口环比', dataIndex: 'mom_export_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_export_pct, b.mom_export_pct), render: formatPctTag },
	              { title: '进口环比', dataIndex: 'mom_import_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.mom_import_pct, b.mom_import_pct), render: formatPctTag },
	              { title: '出口同比', dataIndex: 'yoy_export_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.yoy_export_pct, b.yoy_export_pct), render: formatPctTag },
	              { title: '进口同比', dataIndex: 'yoy_import_pct', width: 92, align: 'right', sorter: (a, b) => compareNumber(a.yoy_import_pct, b.yoy_import_pct), render: formatPctTag }
	            ]}
	          />
        )}

        {detailTab === 'exports' && (
          <Table<CustomsMajorExportRow>
            rowKey={row => commodityTrendKey(row)}
            size="small"
            loading={loading && !snapshot}
            dataSource={filteredMajorExports}
	            pagination={{ pageSize: 10, showSizeChanger: false }}
            tableLayout="fixed"
            scroll={{ x: 960 }}
            rowClassName={row => `customs-trade-clickable-row ${commodityTrendKey(row) === selectedCommodityTrendKey ? 'customs-trade-selected-row' : ''}`}
            onRow={row => ({
              onClick: () => setSelectedCommodityTrendKey(commodityTrendKey(row))
            })}
	            columns={[
	              {
	                title: '方向',
	                dataIndex: 'direction',
	                width: 74,
	                sorter: (a, b) => compareText(directionLabel(a.direction), directionLabel(b.direction)),
	                render: value => <Tag color={value === 'import' ? 'blue' : 'green'}>{directionLabel(value)}</Tag>
	              },
	              {
	                title: '商品',
	                dataIndex: 'commodity',
	                width: 230,
	                sorter: (a, b) => compareText(a.commodity_zh || a.commodity, b.commodity_zh || b.commodity),
	                render: (value, row) => (
	                  <Tooltip title={row.commodity_zh ? value : undefined}>
	                    <Text strong>{row.commodity_zh || value}</Text>
	                  </Tooltip>
	                )
	              },
	              { title: '累计金额', dataIndex: 'ytd_value_usd_mn', width: 104, align: 'right', sorter: (a, b) => compareNumber(a.ytd_value_usd_mn, b.ytd_value_usd_mn), render: value => formatUsd(value) },
	              { title: '当月金额', dataIndex: 'current_value_usd_mn', width: 104, align: 'right', sorter: (a, b) => compareNumber(a.current_value_usd_mn, b.current_value_usd_mn), render: value => formatUsd(value) },
	              { title: '金额环比', dataIndex: 'value_mom_pct', width: 94, align: 'right', sorter: (a, b) => compareNumber(a.value_mom_pct, b.value_mom_pct), render: formatPctTag },
	              { title: '数量环比', dataIndex: 'quantity_mom_pct', width: 94, align: 'right', sorter: (a, b) => compareNumber(a.quantity_mom_pct, b.quantity_mom_pct), render: formatPctTag },
	              { title: '金额同比', dataIndex: 'value_yoy_pct', width: 94, align: 'right', sorter: (a, b) => compareNumber(a.value_yoy_pct, b.value_yoy_pct), render: formatPctTag },
	              {
	                title: '数量单位',
	                dataIndex: 'quantity_unit',
	                width: 96,
	                sorter: (a, b) => compareText(a.quantity_unit, b.quantity_unit),
	                render: value => value && value !== '-' ? value : '—'
	              }
            ]}
          />
        )}
      </Card>

      {snapshot?.sources?.length ? (
        <div className="customs-trade-sources">
          {snapshot.sources.map(source => (
            <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
              <LinkOutlined />
              <span>{source.name}</span>
              <small>{source.note}</small>
            </a>
          ))}
        </div>
      ) : null}
    </CenterShell>
  );
};

const MetricCard: React.FC<{
  icon: React.ReactNode;
  row?: CustomsTotalRow;
  label: string;
  preferYtd?: boolean;
}> = ({ icon, row, label, preferYtd = false }) => {
  const value = preferYtd ? row?.ytd_usd_mn : row?.current_usd_mn;
  const pct = preferYtd ? row?.yoy_ytd_pct : row?.yoy_current_pct;
  const mom = row?.mom_pct;
  const metrics = [
    pct !== null && pct !== undefined ? `${preferYtd ? '累计同比' : '同比'} ${formatSignedPct(pct)}` : null,
    mom !== null && mom !== undefined ? `当月环比 ${formatSignedPct(mom)}` : null
  ].filter(Boolean);

  return (
    <Card>
      <div className="customs-trade-metric">
        <span>{icon}</span>
        <div>
          <Text>{label}</Text>
          <strong>{formatUsd(value, row?.key === 'balance')}</strong>
          <small>
            {totalLabel[row?.key || ''] || row?.item || label}
            {metrics.length ? ` · ${metrics.join(' · ')}` : ''}
          </small>
        </div>
      </div>
    </Card>
  );
};

const AnalysisList: React.FC<{ title: string; items: string[] }> = ({ title, items }) => {
  const visibleItems = (items || []).filter(Boolean).slice(0, 6);
  return (
    <div>
      <Text strong>{title}</Text>
      {visibleItems.length ? (
        <ul className="customs-trade-ai-list">
          {visibleItems.map(item => <li key={item}>{item}</li>)}
        </ul>
      ) : (
        <Paragraph type="secondary">暂无明确结论</Paragraph>
      )}
    </div>
  );
};

const HsDetailPartnerTable: React.FC<{
  title: string;
  rows: CustomsHsDetailPartner[];
  loading: boolean;
}> = ({ title, rows, loading }) => (
  <div className="customs-trade-hs-partner-panel">
    <Text strong>{title}</Text>
    <Table<CustomsHsDetailPartner>
      rowKey={row => `${row.direction}-${row.partner}-${row.value_usd}`}
      size="small"
      loading={loading}
      dataSource={rows}
      pagination={false}
      tableLayout="fixed"
      scroll={{ x: 620 }}
      columns={[
        {
          title: '国家/地区',
          dataIndex: 'partner',
          width: 190,
          sorter: (a, b) => compareText(a.partner_zh || a.partner, b.partner_zh || b.partner),
          render: (value, row) => (
            <Tooltip title={row.partner_zh ? value : undefined}>
              <Text strong>{row.partner_zh || value}</Text>
            </Tooltip>
          )
        },
        { title: '金额', dataIndex: 'value_usd', width: 120, align: 'right', sorter: (a, b) => compareNumber(a.value_usd, b.value_usd), render: value => formatUsdRaw(value) },
        {
          title: '数量',
          dataIndex: 'quantity',
          width: 140,
          align: 'right',
          sorter: (a, b) => compareNumber(a.quantity, b.quantity),
          render: (value, row) => formatQuantityWithUnit(value, row.quantity_unit)
        },
        { title: '单价', dataIndex: 'unit_value_usd', width: 110, align: 'right', sorter: (a, b) => compareNumber(a.unit_value_usd, b.unit_value_usd), render: value => formatUnitValue(value) }
      ]}
    />
  </div>
);

function formatUsd(value?: number | null, signed = false) {
  if (value === null || value === undefined) {
    return '—';
  }
  const prefix = signed && value > 0 ? '+' : value < 0 ? '-' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${prefix}$${(abs / 1_000_000).toFixed(2)}T`;
  }
  if (abs >= 1_000) {
    return `${prefix}$${(abs / 1_000).toFixed(1)}B`;
  }
  return `${prefix}$${abs.toFixed(1)}M`;
}

function formatUsdRaw(value?: number | null, signed = false) {
  if (value === null || value === undefined) {
    return '—';
  }
  const prefix = signed && value > 0 ? '+' : value < 0 ? '-' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) {
    return `${prefix}$${(abs / 1_000_000_000).toFixed(2)}B`;
  }
  if (abs >= 1_000_000) {
    return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  }
  if (abs >= 1_000) {
    return `${prefix}$${(abs / 1_000).toFixed(1)}K`;
  }
  return `${prefix}$${abs.toFixed(0)}`;
}

function formatPctTag(value?: number | null) {
  if (value === null || value === undefined) {
    return <Text type="secondary">—</Text>;
  }
  return (
    <Tag color={value >= 0 ? 'green' : 'red'}>
      {formatSignedPct(value)}
    </Tag>
  );
}

function formatSignedPct(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
}

function formatConfidence(value?: number | null) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function formatQuantity(value?: number | null) {
  if (value === null || value === undefined) {
    return '—';
  }
  return Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 1
  }).format(value);
}

function formatQuantityWithUnit(value?: number | null, unit?: string | null) {
  if (value === null || value === undefined) {
    return '—';
  }
  return `${formatQuantity(value)}${unit ? ` ${unit}` : ''}`;
}

function formatUnitValue(value?: number | null) {
  if (value === null || value === undefined) {
    return '—';
  }
  if (value >= 1000) {
    return `$${value.toFixed(0)}`;
  }
  return `$${value.toFixed(2)}`;
}

function compareNumber(left?: number | null, right?: number | null) {
  const leftValue = left ?? Number.NEGATIVE_INFINITY;
  const rightValue = right ?? Number.NEGATIVE_INFINITY;
  return leftValue - rightValue;
}

function compareText(left?: string | null, right?: string | null) {
  return (left || '').localeCompare(right || '', 'zh-Hans-CN', {
    numeric: true,
    sensitivity: 'base'
  });
}

function displayHsName(row: CustomsHsSectionRow) {
  return row.description_zh || row.description || cleanHsName(row.name);
}

function hsTrendKey(row: CustomsHsSectionRow) {
  if (row.trend_key) {
    return row.trend_key;
  }
  if (row.code) {
    return `chapter:${row.code}`;
  }
  const section = (row.name || '').split(' ', 1)[0];
  return `section:${section || row.name}`;
}

function commodityTrendKey(row: CustomsMajorExportRow) {
  if (row.trend_key) {
    return row.trend_key;
  }
  return `${row.direction || 'export'}:${row.commodity.replace('*', '').trim().toLowerCase()}`;
}

function directionLabel(direction?: string | null) {
  return direction === 'import' ? '进口' : '出口';
}

function trendLineName(name: string) {
  const names: Record<string, string> = {
    trade: '贸易额',
    export: '出口',
    import: '进口',
    value: '金额',
    quantity: '数量'
  };
  return names[name] || name;
}

function toUsdBn(value?: number | null) {
  return value === null || value === undefined ? null : Number((value / 1000).toFixed(1));
}

function toUsdBnRaw(value?: number | null) {
  return value === null || value === undefined ? null : Number((value / 1_000_000_000).toFixed(3));
}

function compactName(value: string) {
  return value
    .replace('United States (US)', 'US')
    .replace('European Union', 'EU')
    .replace('Republic of Korea', 'Korea');
}

function cleanHsName(value: string) {
  return value.replace(/^[\u2160-\u217FIVXLCDM]+\s+/i, '');
}

export default CustomsTradeCenter;
