import React, { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Collapse, Drawer, Empty, Select, Spin, Table, Tabs } from 'antd';
import { DownloadOutlined, FileSearchOutlined, UploadOutlined } from '@ant-design/icons';
import {
  analyzeTradeStatement,
  getTradeStatementTemplate,
  type StatementCurrencyReview,
  type StatementSettlementCurrency,
  type StatementTrade,
  type TradeStatementAnalysis,
} from '../services/riskService';
import { getAuthSnapshot, isAuthRevisionCurrent, registerSessionCleanup, subscribeAuth } from '../state/authSession';
import './TradeStatementReview.css';

const MAX_FILE_BYTES = 2 * 1024 * 1024;
const ACCEPTED_FILES = /\.(csv|tsv|xls|xlsx)$/i;
const currencyLabel: Record<string, string> = { CNY: '人民币', HKD: '港元', USD: '美元' };
const quantityFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 });
const moneyFormat = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const amount = (value: number, currency: string) => `${currency} ${moneyFormat.format(value)}`;
const quantity = (value: number) => quantityFormat.format(value);

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const parts: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    parts.push(String.fromCharCode(...Array.from(bytes.subarray(offset, offset + 32768))));
  }
  return btoa(parts.join(''));
}

function errorMessage(error: unknown, fallback: string): string {
  const candidate = error as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = candidate?.response?.data?.detail;
  return typeof detail === 'string' ? detail : candidate?.message || fallback;
}

function CurrencyReview({ review }: { review: StatementCurrencyReview }) {
  const { currency } = review;
  return (
    <section className="statement-currency-review" aria-label={`${currency} 交易复盘`}>
      {review.unknown_cost_quantity > 0 && (
        <Alert
          type="warning"
          showIcon
          message="有卖出记录缺少买入成本"
          description={`${currency} 有 ${quantity(review.unknown_cost_quantity)} 单位卖出无法确定成本，尚未计入已实现盈亏。请补充期初持仓或更早的买入记录后重新分析。`}
        />
      )}
      <div className="statement-stats">
        <div className="statement-stat statement-stat-primary">
          <span>已实现盈亏 · {currency}</span>
          <strong>{amount(review.realized_pnl, currency)}</strong>
          <small>按先买先卖配对，含已配对交易费用</small>
        </div>
        <div className="statement-stat"><span>交易费用</span><strong>{amount(review.fees, currency)}</strong><small>含未平仓交易的费用</small></div>
        <div className="statement-stat"><span>卖出胜率</span><strong>{review.win_rate === null ? '暂无' : `${review.win_rate.toFixed(1)}%`}</strong><small>{review.closed_trades} 笔已知成本卖出 · {review.winning_trades} 赢 / {review.losing_trades} 亏</small></div>
        <div className="statement-stat"><span>期末未平数量</span><strong>{quantity(review.open_quantity)}</strong><small>各标的未平数量相加仅供核对，不代表仓位或市值</small></div>
      </div>
      <div className="statement-cash-flow">
        <span>买入净支出 <b>{amount(review.buy_amount, currency)}</b></span>
        <span>卖出净收入 <b>{amount(review.sell_amount, currency)}</b></span>
      </div>
      <h3>月度盈亏</h3>
      <Table
        size="small"
        rowKey="month"
        dataSource={review.monthly}
        pagination={{ pageSize: 12, hideOnSinglePage: true }}
        locale={{ emptyText: '暂无月度记录' }}
        columns={[
          { title: '月份', dataIndex: 'month' },
          { title: `已实现盈亏 (${currency})`, dataIndex: 'realized_pnl', align: 'right', render: (value: number) => amount(value, currency) },
          { title: `费用 (${currency})`, dataIndex: 'fees', align: 'right', render: (value: number) => amount(value, currency) },
        ]}
      />
      <h3>个股盈亏排行</h3>
      <Table
        size="small"
        rowKey={row => `${row.market || ''}:${row.symbol}`}
        dataSource={[...review.symbols].sort((left, right) => right.realized_pnl - left.realized_pnl)}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        scroll={{ x: 620 }}
        locale={{ emptyText: '暂无标的记录' }}
        columns={[
          { title: '标的', dataIndex: 'symbol', render: (symbol: string, row) => <span>{row.name || symbol}<small className="statement-symbol">{row.name ? symbol : ''}{row.market ? ` · ${row.market}` : ''}</small></span> },
          { title: `已实现盈亏 (${currency})`, dataIndex: 'realized_pnl', align: 'right', render: (value: number) => amount(value, currency) },
          { title: `费用 (${currency})`, dataIndex: 'fees', align: 'right', render: (value: number) => amount(value, currency) },
          { title: '未平数量', dataIndex: 'open_quantity', align: 'right', render: quantity },
        ]}
      />
    </section>
  );
}

function AnalysisResult({ result }: { result: TradeStatementAnalysis }) {
  return (
    <div className="statement-result" aria-label="交割单分析结果">
      <div className="statement-result-head">
        <div><h3>本次复盘</h3><span>{result.file_name}</span></div>
        <span>{result.total_rows} 行 · {result.trade_count} 笔成交 · {result.skipped_rows} 行未计入</span>
      </div>
      {result.warnings.length > 0 && (
        <Alert type="warning" showIcon message="请先核对以下提示" description={<ul className="statement-warnings">{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>} />
      )}
      <p className="statement-currency-note">按结算币种分别计算，金额不跨币种合计。未找到成本的卖出不计入已实现盈亏。</p>
      {result.currencies.length > 0 ? (
        <Tabs
          aria-label="选择复盘币种"
          items={result.currencies.map(review => ({
            key: review.currency,
            label: `${currencyLabel[review.currency] || review.currency} · ${review.currency}`,
            children: <CurrencyReview review={review} />,
          }))}
        />
      ) : (
        <Empty description="没有可分析的成交记录，请检查文件和下方逐行问题。" />
      )}
      <Collapse
        items={[
          {
            key: 'trades',
            label: `成交预览 · ${result.trades.length} 笔`,
            children: <Table<StatementTrade>
              size="small"
              rowKey={row => `${row.row}-${row.market || ''}-${row.symbol}-${row.side}`}
              dataSource={result.trades}
              pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }}
              scroll={{ x: 960 }}
              locale={{ emptyText: '暂无有效成交' }}
              columns={[
                { title: '原始行', dataIndex: 'row', width: 70 },
                { title: '日期 / 时间', dataIndex: 'date', width: 160, render: (date: string, row) => `${date} ${row.time || ''}` },
                { title: '标的', dataIndex: 'symbol', width: 130, render: (symbol: string, row) => `${row.name || ''} ${symbol}${row.market ? ` · ${row.market}` : ''}`.trim() },
                { title: '方向', dataIndex: 'side', width: 60, render: (side: string) => side === 'buy' ? '买入' : '卖出' },
                { title: '数量', dataIndex: 'quantity', align: 'right', render: quantity },
                { title: '成交价', dataIndex: 'price', align: 'right', render: quantity },
                { title: '成交金额', dataIndex: 'gross_amount', align: 'right', render: (value: number, row) => amount(value, row.currency) },
                { title: '费用', dataIndex: 'fees', align: 'right', render: (value: number, row) => amount(value, row.currency) },
                { title: '结算金额', dataIndex: 'net_amount', align: 'right', render: (value: number, row) => amount(value, row.currency) },
              ]}
            />,
          },
          {
            key: 'issues',
            label: `逐行问题 · ${result.issues.length} 项`,
            children: <Table
              size="small"
              rowKey="key"
              dataSource={result.issues.map((issue, index) => ({ ...issue, key: `${issue.row ?? 'all'}-${issue.code}-${index}` }))}
              pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }}
              locale={{ emptyText: '未发现逐行问题' }}
              columns={[
                { title: '原始行', dataIndex: 'row', width: 80, render: (row: number | null) => row === null ? '整体' : row },
                { title: '需要核对', dataIndex: 'message' },
              ]}
            />,
          },
        ]}
      />
    </div>
  );
}

/** The file and report stay in this mounted panel; no persistent account writes. */
export default function TradeStatementReview() {
  const panelId = `statement-${useId().replace(/:/g, '')}`;
  const authRevision = useSyncExternalStore(subscribeAuth, () => getAuthSnapshot().revision);
  const [file, setFile] = useState<File | null>(null);
  const [settlementCurrency, setSettlementCurrency] = useState<StatementSettlementCurrency | ''>('');
  const [result, setResult] = useState<TradeStatementAnalysis | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const analyzeController = useRef<AbortController | null>(null);
  const templateController = useRef<AbortController | null>(null);
  useEffect(() => {
    const unregister = registerSessionCleanup(() => {
      analyzeController.current?.abort();
      templateController.current?.abort();
      setFile(null);
      setResult(null);
      setSettlementCurrency('');
      setError('');
      setBusy(false);
      setDownloading(false);
    });
    return () => {
      unregister();
      analyzeController.current?.abort();
      templateController.current?.abort();
    };
  }, [authRevision]);

  const chooseFile = (candidate: File | undefined) => {
    if (!candidate) return;
    setResult(null);
    setFile(null);
    if (candidate.size > MAX_FILE_BYTES) {
      setError('文件超过 2 MiB，请按月份或账户拆分后再上传。');
      return;
    }
    if (!ACCEPTED_FILES.test(candidate.name)) {
      setError('请选择 CSV、TSV、XLS 或 XLSX 格式的交割单。');
      return;
    }
    setError('');
    setFile(candidate);
  };

  const analyze = async () => {
    if (!file || busy || downloading) return;
    const controller = new AbortController();
    analyzeController.current = controller;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const content = await fileToBase64(file);
      if (controller.signal.aborted || !isAuthRevisionCurrent(authRevision)) return;
      const response = await analyzeTradeStatement({
        file_name: file.name,
        content_base64: content,
        ...(settlementCurrency ? { settlement_currency: settlementCurrency } : {}),
      }, controller.signal);
      if (!controller.signal.aborted && isAuthRevisionCurrent(authRevision)) setResult(response);
    } catch (failure) {
      if (!controller.signal.aborted && isAuthRevisionCurrent(authRevision)) setError(errorMessage(failure, '交割单分析失败，请检查文件后重试。'));
    } finally {
      if (!controller.signal.aborted && isAuthRevisionCurrent(authRevision)) setBusy(false);
    }
  };

  const downloadTemplate = async () => {
    if (busy || downloading) return;
    const controller = new AbortController();
    templateController.current = controller;
    setDownloading(true);
    setError('');
    try {
      const blob = await getTradeStatementTemplate(controller.signal);
      if (controller.signal.aborted || !isAuthRevisionCurrent(authRevision)) return;
      const url = URL.createObjectURL(blob);
      const revokeUrl = URL.revokeObjectURL.bind(URL);
      const link = document.createElement('a');
      link.href = url;
      link.download = '交割单演示模板.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => revokeUrl(url), 1000);
    } catch (failure) {
      if (!controller.signal.aborted && isAuthRevisionCurrent(authRevision)) setError(errorMessage(failure, '演示模板下载失败，请稍后重试。'));
    } finally {
      if (!controller.signal.aborted && isAuthRevisionCurrent(authRevision)) setDownloading(false);
    }
  };

  return (
    <div className="statement-review">
      <header className="statement-review-intro">
        <div className="statement-review-mark" aria-hidden="true"><FileSearchOutlined /></div>
        <div><h2>看清自己每一笔交易</h2><p>导入券商交割单，按先买先卖配对，核对费用、月度盈亏和个股表现。</p></div>
      </header>
      <p className="statement-privacy">文件仅用于本次计算，不会存储，也不会写入持仓或下单。选择文件后，点击「开始复盘」才会提交。</p>
      <div className="statement-import-card">
        <label className="statement-file-label" htmlFor={`${panelId}-file`}>交割单文件</label>
        <input
          ref={inputRef}
          id={`${panelId}-file`}
          className="statement-file-input"
          type="file"
          accept=".csv,.tsv,.xls,.xlsx"
          disabled={busy || downloading}
          onChange={event => { chooseFile(event.target.files?.[0]); event.target.value = ''; }}
        />
        <div className="statement-file-choice">
          <Button aria-label="选择交割单" icon={<UploadOutlined aria-hidden="true" />} disabled={busy || downloading} onClick={() => inputRef.current?.click()}>选择交割单</Button>
          <span>{file ? `${file.name} · ${Math.max(1, Math.ceil(file.size / 1024))} KB` : '支持 CSV / TSV / XLS（文本导出）/ XLSX，最大 2 MiB'}</span>
        </div>
        <div className="statement-currency-field">
          <label htmlFor={`${panelId}-currency`}>港股通 / 缺失币种的结算币种</label>
          <Select
            id={`${panelId}-currency`}
            aria-label="港股通 / 缺失币种的结算币种"
            value={settlementCurrency}
            disabled={busy || downloading}
            onChange={(value: StatementSettlementCurrency | '') => { setSettlementCurrency(value); setResult(null); setError(''); }}
            options={[
              { value: '', label: '按文件' },
              { value: 'CNY', label: '人民币 · CNY' },
              { value: 'HKD', label: '港元 · HKD' },
              { value: 'USD', label: '美元 · USD' },
            ]}
          />
        </div>
        <p className="statement-settlement-hint">港股通请确认交割单的结算币种与结算金额，港元报价可能与人民币结算金额不同。此选项仅用于港股通和缺失币种的记录，保留其他已注明币种，不会自动换汇。</p>
        <div className="statement-import-actions">
          <Button aria-label={busy ? '正在复盘' : '开始复盘'} type="primary" icon={<FileSearchOutlined aria-hidden="true" />} loading={busy} disabled={!file || downloading} onClick={() => void analyze()}>{busy ? '正在复盘' : '开始复盘'}</Button>
          <Button aria-label="下载演示模板" icon={<DownloadOutlined aria-hidden="true" />} loading={downloading} disabled={busy} onClick={() => void downloadTemplate()}>下载演示模板</Button>
        </div>
        <small className="statement-template-note">模板使用虚构成交，仅用于演示。请替换为自己的记录后再分析。</small>
      </div>
      {error && <Alert type="error" showIcon message="暂未完成复盘" description={error} />}
      {busy && <div className="statement-loading" role="status"><Spin /><span>正在核对成交并计算盈亏…</span></div>}
      {result && <AnalysisResult result={result} />}
      {!busy && !result && !error && <Empty description="选择自己的交割单，开始第一份交易复盘。" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    </div>
  );
}

export function TradeStatementReviewButton({ size = 'middle' }: { size?: 'small' | 'middle' | 'large' }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button aria-label="交割单复盘" className="statement-review-entry" size={size} icon={<FileSearchOutlined aria-hidden="true" />} onClick={() => setOpen(true)}>交割单复盘</Button>
      <Drawer title="交割单复盘" open={open} onClose={() => setOpen(false)} destroyOnClose width="min(1080px, 100vw)" className="statement-review-drawer">
        {open && <TradeStatementReview />}
      </Drawer>
    </>
  );
}
