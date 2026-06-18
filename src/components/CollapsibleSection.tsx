import React from 'react';
import { Collapse } from 'antd';
import { CaretRightOutlined } from '@ant-design/icons';

interface CollapsibleSectionProps {
  title: React.ReactNode;
  extra?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  bordered?: boolean;
  level?: 1 | 2 | 3;
  className?: string;
  style?: React.CSSProperties;
}

const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title,
  extra,
  children,
  defaultOpen = true,
  bordered = false,
  level = 1,
  className,
  style
}) => (
  <Collapse
    className={`collapsible-section collapsible-lv${level} ${bordered ? 'collapsible-bordered' : ''} ${className || ''}`}
    defaultActiveKey={defaultOpen ? ['1'] : []}
    expandIcon={({ isActive }) => (
      <CaretRightOutlined
        rotate={isActive ? 90 : 0}
        className="collapsible-arrow"
      />
    )}
    ghost={!bordered}
    style={style}
    items={[{
      key: '1',
      label: (
        <div className="collapsible-header">
          <span className="collapsible-title">{title}</span>
          {extra && (
            <span
              className="collapsible-extra"
              onClick={(e) => e.stopPropagation()}
            >
              {extra}
            </span>
          )}
        </div>
      ),
      children: <div className="collapsible-body">{children}</div>
    }]}
  />
);

export default CollapsibleSection;
