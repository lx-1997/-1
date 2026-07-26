import React from 'react';
import { Alert, Spin } from 'antd';
import DataQualityBanner from './DataQualityBanner';
import type { DataQuality } from '../../types';
import './CenterShell.css';

/**
 * CenterShell —— 各「中心」页的统一骨架。
 *
 * 把原本每个 Center 各写一遍的「标题栏（eyebrow/title/subtitle + 右侧操作）
 * + 工具条 + 错误提示 + 加载态 + 外层留白」收敛到一处，视觉沿用既有的
 * dashboard-* 样式，所以接入后观感不变，只是去掉重复结构。
 */
export interface CenterShellProps {
  /** 小标签（大写英文短语），如 "A SHARE EARNINGS"。 */
  eyebrow?: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** 标题前的图标。 */
  icon?: React.ReactNode;
  /** 标题栏右侧的操作区（按钮、筛选器、标签等）。 */
  actions?: React.ReactNode;
  /** 标题栏下方的第二行工具条（扫描栏/数据源说明等）。 */
  toolbar?: React.ReactNode;
  /** 错误信息，传入即在工具条下方显示一条 warning。 */
  error?: string | null;
  /** 数据可信度标注，非 live 时在工具条下方显示「演示/降级数据」提示。 */
  dataQuality?: DataQuality | null;
  /** 为 true 时用居中 Spin 替换 body，适合「拉取中 → 出结果」的页面。 */
  loading?: boolean;
  loadingText?: string;
  className?: string;
  children?: React.ReactNode;
}

const CenterShell: React.FC<CenterShellProps> = ({
  eyebrow,
  title,
  subtitle,
  icon,
  actions,
  toolbar,
  error,
  dataQuality,
  loading = false,
  loadingText,
  className,
  children,
}) => (
  <div className={`center-shell${className ? ` ${className}` : ''}`}>
    <div className="center-shell-header">
      <div className="center-shell-heading">
        {eyebrow && <div className="dashboard-eyebrow">{eyebrow}</div>}
        <h2 className="dashboard-title">
          {icon && <span className="center-shell-title-icon">{icon}</span>}
          {title}
        </h2>
        {subtitle && <div className="dashboard-subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="center-shell-actions">{actions}</div>}
    </div>

    {toolbar && <div className="center-shell-toolbar">{toolbar}</div>}

    {error && <Alert className="center-shell-alert" type="warning" showIcon message={error} />}

    <DataQualityBanner quality={dataQuality} className="center-shell-alert" />

    <div className="center-shell-body">
      {loading ? (
        <div className="center-shell-loading">
          <Spin />
          {loadingText && <span>{loadingText}</span>}
        </div>
      ) : (
        children
      )}
    </div>
  </div>
);

export default CenterShell;
