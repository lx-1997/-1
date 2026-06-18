#!/usr/bin/env python3
"""Apply 8 optimization modifications to HomePage.tsx"""

import re

FILE = "/Users/d-robotics/Desktop/超级智能体/-1-main/src/components/HomePage.tsx"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

original = content

# ── 1. Replace React import line (add useCallback, useDeferredValue) ──
content = content.replace(
    "import React, { useEffect, useMemo, useRef, useState } from 'react';",
    "import React, { useCallback, useEffect, useMemo, useRef, useState, useDeferredValue } from 'react';"
)

# ── 2. Add debounce import ──
# Insert after the dataSourceService import
content = content.replace(
    "} from '../services/dataSourceService';",
    "} from '../services/dataSourceService';\nimport { debounce } from '../utils/debounce';"
)

# ── 3. Add useDeferredValue for stocks/posts ──
# Insert after marketSegmentCounts useMemo block (after line ~2253)
content = content.replace(
    """  const activeEngine = agentEngineMeta[agentEngine];""",
    """  const deferredStocks = useDeferredValue(appState.stocks);
  const deferredPosts = useDeferredValue(appState.posts);
  const activeEngine = agentEngineMeta[agentEngine];"""
)

# ── 4. Debounce search handler ──
# Add useMemo before the first use of setResearchKeyword in render context
# Find the <Input that uses researchKeyword and add debounced handler
content = content.replace(
    """  const indexResearchSearch = async () => {""",
    """  const debouncedSetKeyword = useMemo(() => debounce((val: string) => setResearchKeyword(val), 300), []);

  const indexResearchSearch = async () => {"""
)

# Replace the Input onChange for researchKeyword to use debouncedSetKeyword
# The pattern is unique: onChange={event => setResearchKeyword(event.target.value)} right after value={researchKeyword}
content = content.replace(
    """              <Input
                size="small"
                value={researchKeyword}
                onChange={event => setResearchKeyword(event.target.value)}
                onPressEnter={() => void runResearchSearch()}
                placeholder="英伟达 / HBM / AI capex"
              />""",
    """              <Input
                size="small"
                value={researchKeyword}
                onChange={event => debouncedSetKeyword(event.target.value)}
                onPressEnter={() => void runResearchSearch()}
                placeholder="英伟达 / HBM / AI capex"
              />"""
)

# ── 5. Console cleanup → already English, skip ──

# ── 6. Remove message from useEffect dependency array ──
content = content.replace(
    """    message.success('已从研报工作台带入投研工作台');
  }, [appState.currentView, message]);""",
    """    message.success('已从研报工作台带入投研工作台');
  }, [appState.currentView]);"""
)

# ── 7. Fix researchKeyword circular update ──
content = content.replace(
    """    setResearchKeyword(selectedStock.name || selectedStock.symbol);
  }, [researchKeyword, selectedStock]);""",
    """    setResearchKeyword(selectedStock.name || selectedStock.symbol);
  }, [selectedStock]);"""
)

# ── 8. Add aria-label to chat TextArea ──
content = content.replace(
    """        <TextArea
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void submitChat();
            }
          }}
          placeholder="把目标交给 Agent：标的、周期、要判断的问题..."
          autoSize={{ minRows: 1, maxRows: 7 }}
        />""",
    """        <TextArea
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void submitChat();
            }
          }}
          placeholder="把目标交给 Agent：标的、周期、要判断的问题..."
          aria-label="输入投研问题"
          autoSize={{ minRows: 1, maxRows: 7 }}
        />"""
)

# ── 9. Convert 8 render functions to React.memo sub-components ──

# 9a. renderMarketStrip → MarketStripView
content = content.replace(
    """  const renderMarketStrip = () => (
    <div className="market-strip">
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>投研任务</span>
          <span>Running</span>
        </div>
        <div className="market-strip-value">{runningTasks}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>覆盖个股</span>
          <span>Universe</span>
        </div>
        <div className="market-strip-value">{appState.stocks.length}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>A股池</span>
          <span>CN</span>
        </div>
        <div className="market-strip-value">{marketSegmentCounts.aShare}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>港美股池</span>
          <span>HK / US</span>
        </div>
        <div className="market-strip-value">{marketSegmentCounts.global}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>领涨标的</span>
          <span>{strongestStock?.symbol || '--'}</span>
        </div>
        <div className={`market-strip-value ${strongestStock && strongestStock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
          {strongestStock ? `${strongestStock.changePercent >= 0 ? '+' : ''}${strongestStock.changePercent.toFixed(2)}%` : '--'}
        </div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>证据链</span>
          <span>Evidence</span>
        </div>
        <div className="market-strip-value">{sourceItemsCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>行情源</span>
          <span>{quoteAnchor?.symbol || '--'}</span>
        </div>
        <div className="market-strip-value market-strip-value-sm">
          {quoteAnchor ? formatQuoteSourceLine(quoteAnchor) : '--'}
        </div>
        <div className="market-strip-note">{quoteAnchor ? formatQuoteTimestamp(quoteAnchor) : '待刷新'}</div>
      </div>
    </div>
  );""",
    """  const MarketStripView = React.memo((props: {
    runningTasks: number;
    stockCount: number;
    aShareCount: number;
    globalCount: number;
    strongestStock: Stock | undefined;
    sourceItemsCount: number;
    quoteAnchor: Stock | undefined;
  }) => (
    <div className="market-strip">
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>投研任务</span>
          <span>Running</span>
        </div>
        <div className="market-strip-value">{props.runningTasks}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>覆盖个股</span>
          <span>Universe</span>
        </div>
        <div className="market-strip-value">{props.stockCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>A股池</span>
          <span>CN</span>
        </div>
        <div className="market-strip-value">{props.aShareCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>港美股池</span>
          <span>HK / US</span>
        </div>
        <div className="market-strip-value">{props.globalCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>领涨标的</span>
          <span>{props.strongestStock?.symbol || '--'}</span>
        </div>
        <div className={`market-strip-value ${props.strongestStock && props.strongestStock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
          {props.strongestStock ? `${props.strongestStock.changePercent >= 0 ? '+' : ''}${props.strongestStock.changePercent.toFixed(2)}%` : '--'}
        </div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>证据链</span>
          <span>Evidence</span>
        </div>
        <div className="market-strip-value">{props.sourceItemsCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>行情源</span>
          <span>{props.quoteAnchor?.symbol || '--'}</span>
        </div>
        <div className="market-strip-value market-strip-value-sm">
          {props.quoteAnchor ? formatQuoteSourceLine(props.quoteAnchor) : '--'}
        </div>
        <div className="market-strip-note">{props.quoteAnchor ? formatQuoteTimestamp(props.quoteAnchor) : '待刷新'}</div>
      </div>
    </div>
  ));

  const renderMarketStrip = () => (
    <MarketStripView
      runningTasks={runningTasks}
      stockCount={appState.stocks.length}
      aShareCount={marketSegmentCounts.aShare}
      globalCount={marketSegmentCounts.global}
      strongestStock={strongestStock}
      sourceItemsCount={sourceItemsCount}
      quoteAnchor={quoteAnchor}
    />
  );"""
)

# 9b. renderToolchainSpine → ToolchainSpineView
content = content.replace(
    """  const renderToolchainSpine = () => {
    const toolchainSteps: ContextAction[] = [
      {
        key: 'intent',
        title: '投研目标',
        detail: visibleContextStock?.symbol
          ? `${visibleContextStock.name || visibleContextStock.symbol} · ${visibleContextStock.symbol}`
          : visibleContextStock?.name || '组合级任务',
        icon: <RobotOutlined />,
        view: 'home',
        status: modeMeta[chatMode].label
      },
      ...contextActions,
      {
        key: 'runs',
        title: '执行队列',
        detail: `${runningTasks} 个 Run 运行中`,
        icon: <CloudServerOutlined />,
        view: 'agent-center',
        status: activeEngine.shortLabel
      }
    ];

    return (
      <div className="agent-toolchain-spine" aria-label="核心链路工具总览">
        <div className="agent-toolchain-spine-head">
          <span><RobotOutlined /> 核心角色作为入口</span>
          <small>数据、文件、MCP、Skills 和模型都作为可编排工具链注入上下文</small>
        </div>
        <div className="agent-toolchain-spine-grid">
          {toolchainSteps.map((step, index) => (
            <button
              key={step.key}
              type="button"
              className={index === 0 ? 'primary' : ''}
              onClick={() => onViewChange(step.view)}
            >
              <span className="agent-toolchain-index">{index + 1}</span>
              <span className="agent-toolchain-icon">{step.icon}</span>
              <span className="agent-toolchain-copy">
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
              </span>
              <em>{step.status}</em>
            </button>
          ))}
        </div>
      </div>
    );
  };""",
    """  const ToolchainSpineView = React.memo((props: {
    visibleContextStock: StockOption | undefined;
    contextActions: ContextAction[];
    runningTasks: number;
    activeEngine: { shortLabel: string };
    onViewChange: (view: ViewType) => void;
    chatModeLabel: string;
  }) => {
    const toolchainSteps: ContextAction[] = [
      {
        key: 'intent',
        title: '投研目标',
        detail: props.visibleContextStock?.symbol
          ? `${props.visibleContextStock.name || props.visibleContextStock.symbol} · ${props.visibleContextStock.symbol}`
          : props.visibleContextStock?.name || '组合级任务',
        icon: <RobotOutlined />,
        view: 'home',
        status: props.chatModeLabel
      },
      ...props.contextActions,
      {
        key: 'runs',
        title: '执行队列',
        detail: `${props.runningTasks} 个 Run 运行中`,
        icon: <CloudServerOutlined />,
        view: 'agent-center',
        status: props.activeEngine.shortLabel
      }
    ];

    return (
      <div className="agent-toolchain-spine" aria-label="核心链路工具总览">
        <div className="agent-toolchain-spine-head">
          <span><RobotOutlined /> 核心角色作为入口</span>
          <small>数据、文件、MCP、Skills 和模型都作为可编排工具链注入上下文</small>
        </div>
        <div className="agent-toolchain-spine-grid">
          {toolchainSteps.map((step, index) => (
            <button
              key={step.key}
              type="button"
              className={index === 0 ? 'primary' : ''}
              onClick={() => props.onViewChange(step.view)}
            >
              <span className="agent-toolchain-index">{index + 1}</span>
              <span className="agent-toolchain-icon">{step.icon}</span>
              <span className="agent-toolchain-copy">
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
              </span>
              <em>{step.status}</em>
            </button>
          ))}
        </div>
      </div>
    );
  });

  const renderToolchainSpine = () => (
    <ToolchainSpineView
      visibleContextStock={visibleContextStock}
      contextActions={contextActions}
      runningTasks={runningTasks}
      activeEngine={activeEngine}
      onViewChange={onViewChange}
      chatModeLabel={modeMeta[chatMode].label}
    />
  );"""
)

# 9c. renderVisualBoard → VisualBoardView
content = content.replace(
    """  const renderVisualBoard = (variant: 'landing' | 'rail' = 'landing') => (
    <div className={`investor-visual-board investor-visual-board-${variant}`}>
      <CollapsibleSection
        title={<>决策准备度</>}
        extra={<Tag>{modelConfig?.provider || 'model'}</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-readiness-panel"
      >
        <div className="visual-readiness-body">
          <Progress
            type="dashboard"
            percent={decisionReadinessScore}
            size={variant === 'rail' ? 82 : 104}
            strokeColor={decisionReadinessScore >= 72 ? '#12805c' : decisionReadinessScore >= 46 ? '#b7791f' : '#c43e3e'}
          />
          <div className="visual-readiness-copy">
            <strong>{evidenceHealthLabel}</strong>
            <span>{realQuoteCount > 0 ? `${realQuoteCount} 个实时/外部行情源` : '当前主要使用样例行情'}</span>
            <span>{connectedMcpCount}/{mcpServers.length} 工具连接可用</span>
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>核心链路路径</>}
        extra={<Tag>{activeEngine.shortLabel}</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-flow-panel"
      >
        <div className="visual-panel-head">
          <span>核心链路路径</span>
          <Tag>{activeEngine.shortLabel}</Tag>
        </div>
        <div className="visual-agent-flow">
          {agentFlow.map((step, index) => (
            <div key={step.phase} className={`visual-agent-step ${step.active ? 'active' : ''}`}>
              <span className="visual-agent-index">{index + 1}</span>
              <div>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </div>
            </div>
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>波动扫描</>}
        extra={<Tag>{activeStocks.length} 标的</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-market-panel"
      >
        <div className="visual-panel-head">
          <span>波动扫描</span>
          <Tag>{activeStocks.length} 标的</Tag>
        </div>
        <div className="visual-mover-bars">
          {moverChartData.map(item => (
            <div key={item.symbol} className="visual-mover-row">
              <span>{item.symbol}</span>
              <div className="visual-mover-track">
                <i
                  className={item.change >= 0 ? 'positive' : 'negative'}
                  style={{ width: `${Math.max(8, Math.round((Math.abs(item.change) / maxMoverAbs) * 100))}%` }}
                />
              </div>
              <em className={item.change >= 0 ? 'quote-positive' : 'quote-negative'}>
                {item.change >= 0 ? '+' : ''}{item.change}%
              </em>
            </div>
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>风险雷达</>}
        extra={<Tag>{riskPressureScore}%</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-risk-panel"
      >
        <div className="visual-panel-head">
          <span>风险雷达</span>
          <Tag>{riskPressureScore}%</Tag>
        </div>
        <div className="visual-risk-list">
          {riskStocks.map(stock => (
            <button key={stock.symbol} type="button" onClick={() => onStockSelect(stock)}>
              <span>
                <strong>{stock.symbol}</strong>
                <small>{stock.name}</small>
              </span>
              <em className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
              </em>
            </button>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  );""",
    """  const VisualBoardView = React.memo((props: {
    variant: 'landing' | 'rail';
    modelProvider: string;
    decisionReadinessScore: number;
    evidenceHealthLabel: string;
    realQuoteCount: number;
    mcpServerCount: number;
    connectedMcpCount: number;
    activeEngineShortLabel: string;
    agentFlow: { phase: string; label: string; detail: string; active: boolean }[];
    activeStocksLength: number;
    moverChartData: { symbol: string; change: number }[];
    maxMoverAbs: number;
    riskPressureScore: number;
    riskStocks: Stock[];
    onStockSelect: (stock: Stock) => void;
  }) => (
    <div className={`investor-visual-board investor-visual-board-${props.variant}`}>
      <CollapsibleSection
        title={<>决策准备度</>}
        extra={<Tag>{props.modelProvider || 'model'}</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-readiness-panel"
      >
        <div className="visual-readiness-body">
          <Progress
            type="dashboard"
            percent={props.decisionReadinessScore}
            size={props.variant === 'rail' ? 82 : 104}
            strokeColor={props.decisionReadinessScore >= 72 ? '#12805c' : props.decisionReadinessScore >= 46 ? '#b7791f' : '#c43e3e'}
          />
          <div className="visual-readiness-copy">
            <strong>{props.evidenceHealthLabel}</strong>
            <span>{props.realQuoteCount > 0 ? `${props.realQuoteCount} 个实时/外部行情源` : '当前主要使用样例行情'}</span>
            <span>{props.connectedMcpCount}/{props.mcpServerCount} 工具连接可用</span>
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>核心链路路径</>}
        extra={<Tag>{props.activeEngineShortLabel}</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-flow-panel"
      >
        <div className="visual-panel-head">
          <span>核心链路路径</span>
          <Tag>{props.activeEngineShortLabel}</Tag>
        </div>
        <div className="visual-agent-flow">
          {props.agentFlow.map((step, index) => (
            <div key={step.phase} className={`visual-agent-step ${step.active ? 'active' : ''}`}>
              <span className="visual-agent-index">{index + 1}</span>
              <div>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </div>
            </div>
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>波动扫描</>}
        extra={<Tag>{props.activeStocksLength} 标的</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-market-panel"
      >
        <div className="visual-panel-head">
          <span>波动扫描</span>
          <Tag>{props.activeStocksLength} 标的</Tag>
        </div>
        <div className="visual-mover-bars">
          {props.moverChartData.map(item => (
            <div key={item.symbol} className="visual-mover-row">
              <span>{item.symbol}</span>
              <div className="visual-mover-track">
                <i
                  className={item.change >= 0 ? 'positive' : 'negative'}
                  style={{ width: `${Math.max(8, Math.round((Math.abs(item.change) / props.maxMoverAbs) * 100))}%` }}
                />
              </div>
              <em className={item.change >= 0 ? 'quote-positive' : 'quote-negative'}>
                {item.change >= 0 ? '+' : ''}{item.change}%
              </em>
            </div>
          ))}
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={<>风险雷达</>}
        extra={<Tag>{props.riskPressureScore}%</Tag>}
        defaultOpen={true}
        level={3}
        className="visual-panel visual-risk-panel"
      >
        <div className="visual-panel-head">
          <span>风险雷达</span>
          <Tag>{props.riskPressureScore}%</Tag>
        </div>
        <div className="visual-risk-list">
          {props.riskStocks.map(stock => (
            <button key={stock.symbol} type="button" onClick={() => props.onStockSelect(stock)}>
              <span>
                <strong>{stock.symbol}</strong>
                <small>{stock.name}</small>
              </span>
              <em className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
              </em>
            </button>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  ));

  const renderVisualBoard = (variant: 'landing' | 'rail' = 'landing') => (
    <VisualBoardView
      variant={variant}
      modelProvider={modelConfig?.provider || 'model'}
      decisionReadinessScore={decisionReadinessScore}
      evidenceHealthLabel={evidenceHealthLabel}
      realQuoteCount={realQuoteCount}
      mcpServerCount={mcpServers.length}
      connectedMcpCount={connectedMcpCount}
      activeEngineShortLabel={activeEngine.shortLabel}
      agentFlow={agentFlow}
      activeStocksLength={activeStocks.length}
      moverChartData={moverChartData}
      maxMoverAbs={maxMoverAbs}
      riskPressureScore={riskPressureScore}
      riskStocks={riskStocks}
      onStockSelect={onStockSelect}
    />
  );"""
)

# 9d. renderModuleMap → ModuleMapView
content = content.replace(
    """  const renderModuleMap = () => (
    <div className="investor-module-map">
      {moduleGroups.map(group => (
        <section key={group.label} className="module-map-group">
          <div className="module-map-head">
            <strong>{group.label}</strong>
            <span>{group.detail}</span>
          </div>
          <div className="module-map-items">
            {group.items.map(item => (
              <button
                key={item.key}
                type="button"
                className={item.tone || ''}
                onClick={() => onViewChange(item.view)}
              >
                {item.icon}
                <span>{item.title}</span>
                <small>{item.detail}</small>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );""",
    """  const ModuleMapView = React.memo((props: {
    moduleGroups: { label: string; detail: string; items: { key: string; tone?: string; view: ViewType; icon: React.ReactNode; title: string; detail: string }[] }[];
    onViewChange: (view: ViewType) => void;
  }) => (
    <div className="investor-module-map">
      {props.moduleGroups.map(group => (
        <section key={group.label} className="module-map-group">
          <div className="module-map-head">
            <strong>{group.label}</strong>
            <span>{group.detail}</span>
          </div>
          <div className="module-map-items">
            {group.items.map(item => (
              <button
                key={item.key}
                type="button"
                className={item.tone || ''}
                onClick={() => props.onViewChange(item.view)}
              >
                {item.icon}
                <span>{item.title}</span>
                <small>{item.detail}</small>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  ));

  const renderModuleMap = () => (
    <ModuleMapView
      moduleGroups={moduleGroups}
      onViewChange={onViewChange}
    />
  );"""
)

# 9e. renderDecisionPath → DecisionPathView
content = content.replace(
    """  const renderDecisionPath = () => (
    <div className="investor-decision-path" aria-label="投研决策路径">
      <section>
        <span>1</span>
        <strong>定义问题</strong>
        <small>{visibleContextStock?.symbol ? `${visibleContextStock.symbol} · ${modeMeta[chatMode].label}` : visibleContextStock?.name || '标的、组合或事件'}</small>
      </section>
      <section>
        <span>2</span>
        <strong>拉取证据</strong>
        <small>{sourceItemsCount} 条资料 · {realQuoteCount > 0 ? `${realQuoteCount} 个行情源` : '行情待接入'}</small>
      </section>
      <section>
        <span>3</span>
        <strong>输出动作</strong>
        <small>结论、反证、风险纪律和下一步验证</small>
      </section>
    </div>
  );""",
    """  const DecisionPathView = React.memo((props: {
    stockSymbol?: string;
    stockName?: string;
    chatModeLabel: string;
    sourceItemsCount: number;
    realQuoteCount: number;
  }) => (
    <div className="investor-decision-path" aria-label="投研决策路径">
      <section>
        <span>1</span>
        <strong>定义问题</strong>
        <small>{props.stockSymbol ? `${props.stockSymbol} · ${props.chatModeLabel}` : props.stockName || '标的、组合或事件'}</small>
      </section>
      <section>
        <span>2</span>
        <strong>拉取证据</strong>
        <small>{props.sourceItemsCount} 条资料 · {props.realQuoteCount > 0 ? `${props.realQuoteCount} 个行情源` : '行情待接入'}</small>
      </section>
      <section>
        <span>3</span>
        <strong>输出动作</strong>
        <small>结论、反证、风险纪律和下一步验证</small>
      </section>
    </div>
  ));

  const renderDecisionPath = () => (
    <DecisionPathView
      stockSymbol={visibleContextStock?.symbol}
      stockName={visibleContextStock?.name}
      chatModeLabel={modeMeta[chatMode].label}
      sourceItemsCount={sourceItemsCount}
      realQuoteCount={realQuoteCount}
    />
  );"""
)

# 9f. renderInsightRail → InsightRailView
content = content.replace(
    """  const renderInsightRail = () => (
    <aside className="chatgpt-insight-rail">
      {renderVisualBoard('rail')}
      <section className="insight-rail-panel">
        <div className="visual-panel-head">
          <span>当前上下文</span>
          <Tag>{stockOptionToken(visibleContextStock)}</Tag>
        </div>
        <div className="insight-source-list">
          {contextActions.map(action => (
            <button key={action.key} type="button" onClick={() => onViewChange(action.view)}>
              <span className="context-source-icon">{action.icon}</span>
              <span>
                <strong>{action.title}</strong>
                <small>{action.detail}</small>
              </span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  );""",
    """  const InsightRailView = React.memo((props: {
    stockOptionToken: string;
    contextActions: ContextAction[];
    onViewChange: (view: ViewType) => void;
    visualBoardVariant: React.ReactNode;
  }) => (
    <aside className="chatgpt-insight-rail">
      {props.visualBoardVariant}
      <section className="insight-rail-panel">
        <div className="visual-panel-head">
          <span>当前上下文</span>
          <Tag>{props.stockOptionToken}</Tag>
        </div>
        <div className="insight-source-list">
          {props.contextActions.map(action => (
            <button key={action.key} type="button" onClick={() => props.onViewChange(action.view)}>
              <span className="context-source-icon">{action.icon}</span>
              <span>
                <strong>{action.title}</strong>
                <small>{action.detail}</small>
              </span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  ));

  const renderInsightRail = () => (
    <InsightRailView
      stockOptionToken={stockOptionToken(visibleContextStock)}
      contextActions={contextActions}
      onViewChange={onViewChange}
      visualBoardVariant={renderVisualBoard('rail')}
    />
  );"""
)

# 9g. renderReportInsightCards → ReportInsightCardView
content = content.replace(
    """  const renderReportInsightCards = (cards?: ReportInsightCard[]) => {
    if (!cards?.length) return null;

    return (
      <div className="chat-report-card-grid">
        {cards.map(card => {
          const confidenceValue = card.confidence > 1 ? card.confidence : card.confidence * 100;
          const confidenceLabel = confidenceValue > 0
            ? `${Math.round(confidenceValue)}% 置信度`
            : '标题级快筛';
          const flagTitle = card.kind === 'hit-summary' ? '主题线索' : '红旗信号';
          const riskTitle = card.kind === 'hit-summary' ? '风险缺口' : '风险提示';
          const questionTitle = card.kind === 'hit-summary' ? '建议动作' : '后续追问';

          return (
            <section key={card.id} className={`chat-report-card ${card.kind || 'full-report'}`}>
              <div className="chat-report-card-head">
                <span><FileTextOutlined /> {card.kind === 'hit-summary' ? '标题快筛' : '正文解读'}</span>
                <div>
                  <Tag>{confidenceLabel}</Tag>
                  {card.citations > 0 && <Tag color="blue">{card.citations} 引用块</Tag>}
                </div>
              </div>
              <h4>{card.title}</h4>
              <p className="chat-report-card-summary">{card.summary}</p>
              {card.metrics.length > 0 && (
                <div className="chat-report-metrics" aria-label="关键指标">
                  {card.metrics.map(metric => <span key={metric}>{metric}</span>)}
                </div>
              )}
              <div className="chat-report-section-grid">
                {card.flags.length > 0 && (
                  <div className="chat-report-section">
                    <strong><BarChartOutlined /> {flagTitle}</strong>
                    {card.flags.map(point => <span key={point}>{point}</span>)}
                  </div>
                )}
                {card.risks.length > 0 && (
                  <div className="chat-report-section warning">
                    <strong><SafetyCertificateOutlined /> {riskTitle}</strong>
                    {card.risks.map(risk => <span key={risk}>{risk}</span>)}
                  </div>
                )}
                {card.questions.length > 0 && (
                  <div className="chat-report-section action">
                    <strong><FileSearchOutlined /> {questionTitle}</strong>
                    {card.questions.map(question => <span key={question}>{question}</span>)}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    );
  };""",
    """  const ReportInsightCardView = React.memo((props: { cards?: ReportInsightCard[] }) => {
    if (!props.cards?.length) return null;

    return (
      <div className="chat-report-card-grid">
        {props.cards.map(card => {
          const confidenceValue = card.confidence > 1 ? card.confidence : card.confidence * 100;
          const confidenceLabel = confidenceValue > 0
            ? `${Math.round(confidenceValue)}% 置信度`
            : '标题级快筛';
          const flagTitle = card.kind === 'hit-summary' ? '主题线索' : '红旗信号';
          const riskTitle = card.kind === 'hit-summary' ? '风险缺口' : '风险提示';
          const questionTitle = card.kind === 'hit-summary' ? '建议动作' : '后续追问';

          return (
            <section key={card.id} className={`chat-report-card ${card.kind || 'full-report'}`}>
              <div className="chat-report-card-head">
                <span><FileTextOutlined /> {card.kind === 'hit-summary' ? '标题快筛' : '正文解读'}</span>
                <div>
                  <Tag>{confidenceLabel}</Tag>
                  {card.citations > 0 && <Tag color="blue">{card.citations} 引用块</Tag>}
                </div>
              </div>
              <h4>{card.title}</h4>
              <p className="chat-report-card-summary">{card.summary}</p>
              {card.metrics.length > 0 && (
                <div className="chat-report-metrics" aria-label="关键指标">
                  {card.metrics.map(metric => <span key={metric}>{metric}</span>)}
                </div>
              )}
              <div className="chat-report-section-grid">
                {card.flags.length > 0 && (
                  <div className="chat-report-section">
                    <strong><BarChartOutlined /> {flagTitle}</strong>
                    {card.flags.map(point => <span key={point}>{point}</span>)}
                  </div>
                )}
                {card.risks.length > 0 && (
                  <div className="chat-report-section warning">
                    <strong><SafetyCertificateOutlined /> {riskTitle}</strong>
                    {card.risks.map(risk => <span key={risk}>{risk}</span>)}
                  </div>
                )}
                {card.questions.length > 0 && (
                  <div className="chat-report-section action">
                    <strong><FileSearchOutlined /> {questionTitle}</strong>
                    {card.questions.map(question => <span key={question}>{question}</span>)}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    );
  });

  const renderReportInsightCards = (cards?: ReportInsightCard[]) => (
    <ReportInsightCardView cards={cards} />
  );"""
)

# 9h. renderGuidePanel → GuidePanelView
content = content.replace(
    """  const renderGuidePanel = (guide?: ChatGuidePanel) => {
    if (!guide) return null;

    return (
      <section className={`chat-guide-panel ${guide.variant}`}>
        <div className="chat-guide-head">
          <span>
            {guide.variant === 'research' ? <FileSearchOutlined /> : guide.variant === 'error' ? <SafetyCertificateOutlined /> : <RobotOutlined />}
            {guide.title}
          </span>
          {guide.variant === 'generating' && <Tag className="chat-guide-live-tag">生成中</Tag>}
        </div>
        {guide.description && <p>{guide.description}</p>}
        {guide.steps && guide.steps.length > 0 && (
          <div className="chat-guide-steps">
            {guide.steps.map(step => (
              <span key={`${guide.title}-${step.label}`} className={step.status}>
                <i />
                {step.label}""",
    """  const GuidePanelView = React.memo((props: { guide?: ChatGuidePanel }) => {
    if (!props.guide) return null;

    return (
      <section className={`chat-guide-panel ${props.guide.variant}`}>
        <div className="chat-guide-head">
          <span>
            {props.guide.variant === 'research' ? <FileSearchOutlined /> : props.guide.variant === 'error' ? <SafetyCertificateOutlined /> : <RobotOutlined />}
            {props.guide.title}
          </span>
          {props.guide.variant === 'generating' && <Tag className="chat-guide-live-tag">生成中</Tag>}
        </div>
        {props.guide.description && <p>{props.guide.description}</p>}
        {props.guide.steps && props.guide.steps.length > 0 && (
          <div className="chat-guide-steps">
            {props.guide.steps.map(step => (
              <span key={`${props.guide.title}-${step.label}`} className={step.status}>
                <i />
                {step.label}"""
)

# Write back
with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("All modifications applied successfully.")
print(f"File size: {len(original)} → {len(content)} chars")
