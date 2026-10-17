/**
 * 稻草财经 UI Kit — 统一导出面
 *
 * 用法：
 *   import { Button, Modal, useToast, ToastProvider } from '../components/ui';
 *   import '../components/ui/ui.css';   // 应用入口处引入一次即可
 *
 * 详见同目录 README.md（架构、Props 契约、可访问性说明与示例）。
 */

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
