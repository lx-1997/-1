# 稻草财经 UI Kit（`src/components/ui`）

生产级可复用组件库。零第三方运行时依赖，全部视觉值消费全局 CSS 令牌（`src/index.css`），双主题（light/dark）与五色 accent（`data-accent`）自动生效，无需组件级配置。

## 组件架构

```
ui/
├── types.ts        共享类型（DuiSize/DuiTone）、cx 类名组合器
├── icons.tsx       内联装饰性 SVG（无外部图标依赖）
├── ui.css          全部样式，dui- 前缀，只引用令牌
├── index.ts        统一导出面
├── 基础层   Spinner / Skeleton / Badge / Card / EmptyState
├── 控件层   Button / IconButton / Input / Tabs(+TabPanel)
├── 浮层层   Modal / Toast(+Provider) / Tooltip        ← Portal 到 body
└── examples/UIKitShowcase.tsx   可运行示例（默认不接入路由）
```

分层规则：浮层组件只通过 `createPortal` 渲染；基础层与控件层不感知浮层；组件间互不依赖（Button→Spinner 除外）。样式与逻辑同组件文件就近维护，主题切换零成本。

## 接入

```tsx
// 应用入口引入一次样式
import './components/ui/ui.css';

// 业务代码按需导入
import { Button, Modal, ToastProvider, useToast } from './components/ui';
```

`<ToastProvider>` 需包裹在使用 `useToast` 的组件树外层（建议挂在 App 根部）。

## Props 契约摘要

### Button

| Prop | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `variant` | `'primary' \| 'secondary' \| 'ghost' \| 'danger'` | `primary` | 视觉权重分级 |
| `size` | `'sm' \| 'md' \| 'lg'` | `md` | 高度 28/34/42 |
| `loading` | `boolean` | `false` | 显示 Spinner、强制禁点、`aria-busy` |
| `block` | `boolean` | `false` | 占满容器宽 |
| `icon` | `ReactNode` | — | 左侧装饰图标 |
| `type` | — | `'button'` | 显式缺省，避免表单误提交 |

继承全部原生 button 属性；`loading` 与 `disabled` 任一为真即不可交互。

### IconButton

与 Button 同套变体/尺寸；`ariaLabel` 为**必填**（类型层强制）——图标按钮没有可见文本，读屏名称不可缺失。

### Input

| Prop | 说明 |
| --- | --- |
| `label` / `required` | 自动接线 `htmlFor`/`id`、`aria-required` 与视觉红星 |
| `hint` / `error` | 自动接线 `aria-describedby`；`error` 同时置 `aria-invalid`，并隐藏 hint |
| `prefix` / `suffix` | 前后缀插槽（单位、币种等） |
| `size` / `block` | 30/36/42 三档高度；默认占满容器 |

id 由组件内部生成，调用方不手工维护。

### Modal

| Prop | 默认 | 说明 |
| --- | --- | --- |
| `open` / `onClose` | — | 受控开关；三种关闭途径（Esc/遮罩/关闭按钮）统一回调 |
| `title` / `ariaLabel` | — | 无障碍名称：有 title 走 `aria-labelledby`，否则用 `ariaLabel` |
| `size` | `md` | 400/560/760；≤640px 视口自动转为底部抽屉 |
| `closeOnEsc` / `closeOnBackdrop` | `true` | 遮罩关闭要求按下与抬起都落在遮罩上，模态内拖拽选区不误关 |
| `closable` | `true` | 强引导流程（支付、绑定）可隐藏关闭按钮 |
| `initialFocusRef` | — | 初始聚焦目标；缺省聚焦第一个可聚焦元素 |

内置行为：焦点圈养（Tab/Shift+Tab 限制在弹窗内）、关闭后焦点还原、body 滚动锁定（含滚动条宽度补偿）、多层弹窗时仅栈顶响应 Esc、滚动锁按引用计数共享。

### Toast（`ToastProvider` + `useToast`）

```tsx
const toast = useToast();
toast.success('已保存');
toast.error('同步失败', '配额不足，请稍后重试', 6000); // 标题、描述、时长
toast.show({ type: 'warning', title: '延迟偏高', duration: 0 }); // duration<=0 常驻
```

视口 `aria-live="polite"`，错误条目 `role="alert"` 即时播报；最多同屏 4 条（超出丢最旧）；指针悬停暂停计时、移出按剩余时长恢复。

### Tabs + TabPanel

```tsx
<Tabs items={[{ key: 'a', label: '概览' }, { key: 'b', label: '纪要', disabled: true }]}
      activeKey={tab} onChange={setTab} ariaLabel="数据区">
  <TabPanel tabKey="a">…</TabPanel>
  <TabPanel tabKey="b">…</TabPanel>
</Tabs>
```

完整 WAI-ARIA tabs 语义（roving tabindex、方向键/Home/End 循环跳过禁用项、自动/手动两档激活、`aria-controls`/`aria-labelledby` 自动接线）。`TabPanel` 必须作为 `Tabs` 的子元素渲染（内部上下文提供 id 接线与激活状态）；`keepMounted` 用于保留面板内部状态。

### Tooltip

hover 与键盘 focus 双触发；Portal 定位不受 `overflow` 裁剪；空间不足自动翻转 top/bottom；滚动（capture）与 resize 实时重算，Esc 立即隐藏。子元素为原生标签时以 `cloneElement` 注入 `aria-describedby` 与事件（不产生包裹节点）；自定义组件子元素则退化为 inline-flex 包裹层，组件须透传 props 到实际 DOM。

### 基础层

- **Spinner**：`label` 提供时以 `role="status"` 播报，否则对读屏器隐藏。
- **Skeleton**：`variant` text/circular/rectangular；`lines` 多行末行 60% 收尾；`srLabel` 声明加载状态。
- **Badge**：六种 `tone` 映射语义令牌；`dot` 实心状态点。
- **Card**：纯展示表面，不提供整卡可点击（交互语义交给内部控件）。
- **EmptyState**：`title` 必填，`description`/`action` 可选。

## 边界与已知约束

1. 组件样式只保证在令牌体系内工作；绕过令牌硬编码颜色会在主题切换时失真。
2. `Toast`/`Tooltip`/`Modal` 渲染进 `document.body`，z-index 标尺为 backdrop 1000 < modal 1001 < toast 1100 < tooltip 1200；业务自定义浮层请沿用该标尺。
3. `Tooltip` 的自定义组件子元素若不透传 `onMouseEnter/onFocus` 等属性，提示不会触发。
4. `Skeleton` 的 circular 变体需显式 `width` 与 `height`。
5. `Modal` 无退出动画（渲染即移除），换取行为确定性；入场有 200ms 过渡，`prefers-reduced-motion` 下自动关闭。

## 测试

组件测试位于 `src/__tests__/ui/`，覆盖：加载态交互、焦点圈养与还原、Esc 栈顶语义、键盘导航、ARIA 接线、通知计时与容量淘汰。

```bash
CI=true npx react-scripts test --watchAll=false --testPathPattern='__tests__/ui'
```
