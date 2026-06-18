import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Avatar,
  Button,
  Empty,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message
} from 'antd';
import {
  BulbOutlined,
  ExperimentOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
  WarningOutlined,
  ApiOutlined,
  LineChartOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import { AiResearchReport, analyzeStock, checkAiApiHealth } from '../services/researchService';
import DataQualityBanner from './common/DataQualityBanner';
import { useT } from '../i18n/useT';
import './AiResearch.css';

const { Text } = Typography;
const { TextArea } = Input;

interface AiResearchProps {
  appState: AppState;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  report?: AiResearchReport;
}

const sentimentMap = {
  positive: { text: '偏积极', color: 'var(--positive)' },
  neutral: { text: '中性', color: 'var(--info)' },
  negative: { text: '偏谨慎', color: 'var(--negative)' }
};

const riskMap = {
  low: { text: '低', color: 'var(--positive)' },
  medium: { text: '中', color: 'var(--warning)' },
  high: { text: '高', color: 'var(--negative)' }
};

function generateId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

const AiResearch: React.FC<AiResearchProps> = ({ appState }) => {
  const t = useT();
  const [selectedSymbol, setSelectedSymbol] = useState(appState.stocks[0]?.symbol);
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<{ status: string; provider: string; model: string } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);

  const selectedStock = useMemo(
    () => appState.stocks.find(stock => stock.symbol === selectedSymbol) || appState.stocks[0],
    [appState.stocks, selectedSymbol]
  );

  const relatedPosts = useMemo(
    () => selectedStock
      ? appState.posts.filter(post => post.stockSymbol === selectedStock.symbol).slice(0, 8)
      : [],
    [appState.posts, selectedStock]
  );

  useEffect(() => {
    checkAiApiHealth()
      .then(setApiStatus)
      .catch(() => setApiStatus(null));
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (selectedStock) {
      setMessages([]);
    }
  }, [selectedSymbol]);

  const handleSend = async () => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion && messages.length > 0) {
      message.info('请输入您的问题');
      return;
    }

    if (!selectedStock) {
      message.warning('暂无可分析的股票');
      return;
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmedQuestion || `分析 ${selectedStock.name} (${selectedStock.symbol}) 的投资价值`,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMsg]);
    setQuestion('');
    setLoading(true);

    try {
      const data = await analyzeStock({
        stock: selectedStock,
        posts: relatedPosts.map(post => ({
          title: post.title,
          summary: post.summary,
          content: post.content,
          category: post.category,
          tags: post.tags,
          qualityScore: post.qualityScore,
          publishTime: post.publishTime
        })),
        question: userMsg.content,
        locale: 'zh-CN'
      });

      const assistantMsg: ChatMessage = {
        id: generateId(),
        role: 'assistant',
        content: data.executive_summary,
        timestamp: Date.now(),
        report: data
      };

      setMessages(prev => [...prev, assistantMsg]);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动并配置模型';
      const errorMsg: ChatMessage = {
        id: generateId(),
        role: 'system',
        content: `${t('ai.failed')}：${detail}`,
        timestamp: Date.now()
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const renderReportCard = (report: AiResearchReport) => {
    const sentiment = sentimentMap[report.sentiment_label];
    const risk = riskMap[report.risk_level];

    return (
      <div className="ai-chat-report-card">
        <DataQualityBanner quality={report.data_quality} />
        <div className="ai-chat-report-stats">
          <div className="ai-chat-stat">
            <span className="ai-chat-stat-label">情绪判断</span>
            <span className="ai-chat-stat-value" style={{ color: sentiment?.color }}>
              {sentiment?.text} · {report.sentiment_score.toFixed(1)}
            </span>
          </div>
          <div className="ai-chat-stat">
            <span className="ai-chat-stat-label">风险等级</span>
            <span className="ai-chat-stat-value" style={{ color: risk?.color }}>
              {risk?.text}
            </span>
          </div>
          <div className="ai-chat-stat">
            <span className="ai-chat-stat-label">分析模型</span>
            <span className="ai-chat-stat-value">{report.model}</span>
          </div>
        </div>

        {report.catalysts.length > 0 && (
          <div className="ai-chat-section">
            <div className="ai-chat-section-title">
              <BulbOutlined /> 催化因素
            </div>
            <ul className="ai-chat-list">
              {report.catalysts.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {report.risks.length > 0 && (
          <div className="ai-chat-section">
            <div className="ai-chat-section-title">
              <WarningOutlined /> 主要风险
            </div>
            <ul className="ai-chat-list">
              {report.risks.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {report.watch_items.length > 0 && (
          <div className="ai-chat-section">
            <div className="ai-chat-section-title">
              <ApiOutlined /> 观察清单
            </div>
            <ul className="ai-chat-list">
              {report.watch_items.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {report.suggested_questions.length > 0 && (
          <div className="ai-chat-section">
            <div className="ai-chat-section-title">
              <ExperimentOutlined /> 追问方向
            </div>
            <div className="ai-chat-suggestions">
              {report.suggested_questions.map((q, i) => (
                <button
                  key={i}
                  className="ai-chat-suggestion-chip"
                  onClick={() => {
                    setQuestion(q);
                    setTimeout(() => inputRef.current?.focus(), 100);
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="ai-chat-disclaimer">
          {report.disclaimer}
        </div>
      </div>
    );
  };

  const renderMessageContent = (msg: ChatMessage) => {
    if (msg.role === 'system') {
      return (
        <div className="ai-chat-system-msg">
          <WarningOutlined style={{ marginRight: 8 }} />
          {msg.content}
        </div>
      );
    }

    if (msg.report) {
      return (
        <div>
          <div className="ai-chat-bubble-text">{msg.content}</div>
          {renderReportCard(msg.report)}
        </div>
      );
    }

    return <div className="ai-chat-bubble-text">{msg.content}</div>;
  };

  return (
    <div className="ai-chat-container">
      <div className="ai-chat-header">
        <div className="ai-chat-header-left">
          <Select
            value={selectedSymbol}
            onChange={setSelectedSymbol}
            options={appState.stocks.map(stock => ({
              value: stock.symbol,
              label: `${stock.name} (${stock.symbol})`
            }))}
            style={{ minWidth: 220 }}
            size="middle"
            bordered={false}
            className="ai-chat-stock-select"
          />
          {selectedStock && (
            <div className="ai-chat-price-info">
              <span className="ai-chat-price">${selectedStock.currentPrice.toFixed(2)}</span>
              <span className={selectedStock.changePercent >= 0 ? 'ai-chat-change-up' : 'ai-chat-change-down'}>
                {selectedStock.changePercent >= 0 ? '+' : ''}{selectedStock.changePercent.toFixed(2)}%
              </span>
            </div>
          )}
        </div>
        <div className="ai-chat-header-right">
          {apiStatus ? (
            <Tag color="green" style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.25)' }}>
              <LineChartOutlined /> {apiStatus.provider}
            </Tag>
          ) : (
            <Tag color="orange" style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.25)' }}>
              {t('ai.apiDisconnected')}
            </Tag>
          )}
        </div>
      </div>

      <div className="ai-chat-messages">
        {messages.length === 0 ? (
          <div className="ai-chat-empty">
            <div className="ai-chat-empty-icon">
              <RobotOutlined />
            </div>
            <div className="ai-chat-empty-title">{t('ai.title')}</div>
            <div className="ai-chat-empty-desc">
              {t('ai.desc')}
            </div>
            <div className="ai-chat-empty-hints">
              <button
                className="ai-chat-hint-chip"
                onClick={() => {
                  setQuestion('分析这只股票的核心竞争力和护城河');
                  setTimeout(() => inputRef.current?.focus(), 100);
                }}
              >
                {t('ai.hint1')}
              </button>
              <button
                className="ai-chat-hint-chip"
                onClick={() => {
                  setQuestion('当前估值是否合理？有哪些风险需要关注？');
                  setTimeout(() => inputRef.current?.focus(), 100);
                }}
              >
                {t('ai.hint2')}
              </button>
              <button
                className="ai-chat-hint-chip"
                onClick={() => {
                  setQuestion('近期的催化因素和潜在机会是什么？');
                  setTimeout(() => inputRef.current?.focus(), 100);
                }}
              >
                {t('ai.hint3')}
              </button>
            </div>
          </div>
        ) : (
          messages.map(msg => (
            <div key={msg.id} className={`ai-chat-message ai-chat-message-${msg.role}`}>
              <div className="ai-chat-message-inner">
                {msg.role === 'assistant' && (
                  <Avatar
                    className="ai-chat-avatar"
                    icon={<RobotOutlined />}
                    style={{ backgroundColor: 'var(--accent)', flexShrink: 0 }}
                  />
                )}
                <div className={`ai-chat-bubble ai-chat-bubble-${msg.role}`}>
                  {renderMessageContent(msg)}
                </div>
                {msg.role === 'user' && (
                  <Avatar
                    className="ai-chat-avatar"
                    icon={<UserOutlined />}
                    style={{ backgroundColor: 'var(--info)', flexShrink: 0 }}
                  />
                )}
              </div>
            </div>
          ))
        )}

        {loading && (
          <div className="ai-chat-message ai-chat-message-assistant">
            <div className="ai-chat-message-inner">
              <Avatar
                className="ai-chat-avatar"
                icon={<RobotOutlined />}
                style={{ backgroundColor: 'var(--accent)', flexShrink: 0 }}
              />
              <div className="ai-chat-bubble ai-chat-bubble-assistant ai-chat-bubble-loading">
                <div className="ai-chat-typing">
                  <span /><span /><span />
                </div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="ai-chat-input-area">
        <div className="ai-chat-input-wrapper">
          <TextArea
            ref={inputRef}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={messages.length === 0 ? String(t('ai.placeholder')) : String(t('ai.continueAsk'))}
            autoSize={{ minRows: 1, maxRows: 6 }}
            disabled={loading}
            className="ai-chat-input"
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            loading={loading}
            disabled={!question.trim() && messages.length > 0}
            className="ai-chat-send-btn"
          />
        </div>
        <div className="ai-chat-input-hint">
          {t('ai.sendHint')}
        </div>
      </div>
    </div>
  );
};

export default React.memo(AiResearch);