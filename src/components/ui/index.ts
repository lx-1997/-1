/**
 * 稻草财经 UI Kit — 统一导出面
 *
 * 样式随本模块自包含：任何业务代码导入本 barrel 即自动带入 ui.css，
 * 无需（也不应）在应用入口单独引入样式文件。
 *
 * 用法：
 *   import { Button, Modal, useToast, ToastProvider } from '../components/ui';
 *
 * 详见同目录 README.md（架构、Props 契约、可访问性说明与示例）。
 */

import './ui.css';

export { Button, type ButtonProps, type ButtonVariant } from './Button';
export { IconButton, type IconButtonProps } from './IconButton';
export { Input, type InputProps } from './Input';
export { Spinner, type SpinnerProps } from './Spinner';
export { Skeleton, type SkeletonProps } from './Skeleton';
export { Badge, type BadgeProps } from './Badge';
export { Card, type CardProps } from './Card';
export { EmptyState, type EmptyStateProps } from './EmptyState';
export { Modal, type ModalProps } from './Modal';
export { Tabs, TabPanel, type TabsProps, type TabsItem, type TabPanelProps } from './Tabs';
export { Tooltip, type TooltipProps } from './Tooltip';
export {
  ToastProvider,
  useToast,
  type ToastApi,
  type ToastOptions,
  type ToastType,
} from './Toast';

export { cx, type DuiSize, type DuiTone } from './types';
