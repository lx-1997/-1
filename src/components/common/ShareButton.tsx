import React, { useCallback, useState } from 'react';
import { ShareAltOutlined } from '@ant-design/icons';
import ShareModal, { ShareTarget } from '../ShareModal';
import './ShareButton.css';

interface ShareButtonProps {
  /** 分享目标；传函数则在点击时惰性构造（结论较大时按需生成）。 */
  target: ShareTarget | (() => ShareTarget);
  modalTitle?: string;
  /** 自定义按钮类名；缺省用 .df-share-btn 默认样式。 */
  className?: string;
  /** 悬浮提示。 */
  tooltip?: string;
  /** 自定义按钮内容；缺省「分享」。 */
  children?: React.ReactNode;
}

/**
 * 通用分享按钮：自带 ShareModal 状态，任意「AI 结论」面一行接入，
 * 不必在每个页面重复写 state + 弹窗渲染。
 */
const ShareButton: React.FC<ShareButtonProps> = ({
  target,
  modalTitle = '分享投研结论',
  className,
  tooltip = '把这段结论生成分享文案',
  children,
}) => {
  const [active, setActive] = useState<ShareTarget | null>(null);

  const open = useCallback(() => {
    setActive(typeof target === 'function' ? target() : target);
  }, [target]);

  return (
    <>
      <button type="button" className={className || 'df-share-btn'} onClick={open} title={tooltip}>
        {children ?? <><ShareAltOutlined /> 分享</>}
      </button>
      <ShareModal
        visible={Boolean(active)}
        content={active ?? undefined}
        modalTitle={modalTitle}
        onCancel={() => setActive(null)}
      />
    </>
  );
};

export default ShareButton;
