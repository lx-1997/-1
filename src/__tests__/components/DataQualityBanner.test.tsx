import React from 'react';
import { render } from '@testing-library/react';
import DataQualityBanner from '../../components/common/DataQualityBanner';

describe('DataQualityBanner', () => {
  it('mock 级别显示醒目的「演示数据」红色提示', () => {
    const { getByText, container } = render(
      <DataQualityBanner quality={{ level: 'mock', label: '演示数据', detail: '不能作为投资依据', reasons: [] }} />
    );
    expect(getByText('演示数据')).toBeInTheDocument();
    expect(getByText('不能作为投资依据')).toBeInTheDocument();
    // mock → antd Alert error（红）
    expect(container.querySelector('.ant-alert-error')).toBeTruthy();
  });

  it('degraded 级别显示降级警告（黄）', () => {
    const { getByText, container } = render(
      <DataQualityBanner quality={{ level: 'degraded', label: '降级兜底', detail: '本地规则兜底', reasons: [] }} />
    );
    expect(getByText('降级兜底')).toBeInTheDocument();
    expect(container.querySelector('.ant-alert-warning')).toBeTruthy();
  });

  it('reasons 列表逐条渲染', () => {
    const { getByText } = render(
      <DataQualityBanner quality={{ level: 'degraded', label: '降级兜底', detail: '', reasons: ['云模型超时', '已用本地规则'] }} />
    );
    expect(getByText('云模型超时')).toBeInTheDocument();
    expect(getByText('已用本地规则')).toBeInTheDocument();
  });

  it('live 级别不渲染任何内容（真实数据不打扰）', () => {
    const { container } = render(
      <DataQualityBanner quality={{ level: 'live', label: '', detail: '', reasons: [] }} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('quality 缺失时不渲染', () => {
    const { container } = render(<DataQualityBanner quality={null} />);
    expect(container.firstChild).toBeNull();
  });
});
