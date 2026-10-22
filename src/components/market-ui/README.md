# 稻草财经行情 UI 组件

面向观察池、行情侧栏和个股概览的六个 React + TypeScript 组件。使用现有全局主题令牌，不依赖 Ant Design 或尚未接入的 `components/ui`；从统一入口导入会自动载入组件样式。

## 架构

```text
数据服务 / 业务容器（请求、轮询、账户隔离、持久化）
  ↓ 转换为 QuoteItem；显式声明数据质量
WatchlistPanel（搜索、排序、请求锁、操作错误）
  ├─ AsyncState（首次加载 / 后台刷新 / 请求失败）
  ├─ QuoteCard（身份、选中状态、监控操作）
  │    ├─ QuoteValue（报价、涨跌方向、币种）
  │    └─ QuoteStatus（数据质量、来源、时间）
  └─ AsyncButton（异步防重复、保留焦点、失败恢复）
```

数据请求由容器注入，组件不读取账户、本地存储或网络，也不把组件演示中的价格用作业务行情。选中标的和监控状态由调用者管理；搜索、排序、正在执行的动作由面板管理。

## 数据与 Props 设计

`QuoteItem` 是标准化的显示模型：

```tsx
type QuoteItem = {
  id: string;                    // 稳定唯一键，例如 HK:0700，不能使用列表索引
  symbol: string;
  name: string;
  market?: 'CN' | 'HK' | 'US' | 'OTHER';
  currency?: string;             // CNY / HKD / USD，未知单位也安全显示
  price: number | null;          // 缺失用 null，0 是合法值
  changePercent: number | null;  // 百分点：1.25 表示 1.25%，不能传 0.0125
  quality: 'live' | 'delayed' | 'stale' | 'mock' | 'unavailable';
  source?: string;
  updatedAt?: string | null;     // 带 Z 或时区偏移的 ISO 时间
  delayNote?: string;
};
```

| 组件 | 核心 Props | 行为 |
| --- | --- | --- |
| `QuoteValue` | `value`，`kind='price'`，`currency`，`colorConvention='cn'` | 零值保留；非有限值视为缺失；价格 2–4 位小数；百分比两位小数 |
| `QuoteStatus` | `quality`，`source`，`updatedAt`，`delayNote`，`compact=false` | 时间统一北京时间；无时区或非法时间显示“时间待核验”；compact 保留读屏元数据 |
| `QuoteCard` | `item`，`selected`，`watched`，`pending`，`onSelect`，`onToggleWatch`，`colorConvention` | 整卡为展示容器；详情和监控是独立原生按钮；不可用行情隐藏残留数字 |
| `AsyncButton` | `onAction`，`busy`，`disabled`，`busyLabel`，`onError`，`variant` | 同步请求锁；Promise 结束后解锁；忙碌时使用 `aria-disabled` 保留焦点并阻止重复原生提交 |
| `AsyncState` | `status`，`hasData`，`error`，`onRetry`，`children` | 首次加载骨架屏；有旧数据则保留；无旧数据的失败显示错误与重试 |
| `WatchlistPanel` | 见下表 | 组合观察池，支持搜索、排序和后台刷新 |

`AsyncButton` 继承原生 button 属性并透传 ref，默认 `type="button"`。异步动作的异常由 `onError` 接收；未提供时显示通用失败信息。业务表单通过 `onSubmit` 管理验证与提交。

| WatchlistPanel Prop | 类型 / 默认值 | 说明 |
| --- | --- | --- |
| `items` | `readonly QuoteItem[]`，必填 | 不会原地排序或修改传入数据 |
| `title` | `string`，`观察池` | 同时作为区域无障碍名称 |
| `status` | `'loading' \| 'ready' \| 'error'`，`ready` | 整体请求状态 |
| `refreshing` | `boolean`，`false` | 外部轮询中的刷新状态；组件自己的刷新也自动跟踪 |
| `error` | `string` | 请求失败信息，保留旧数据时同时提示 |
| `selectedId` | `string` | 受控选中标的 |
| `watchedIds` / `pendingIds` | `readonly string[]`，`[]` | 受控监控状态及其他页面发起的待完成动作 |
| `onRefresh` | `() => void \| Promise<void>` | 返回真实请求 Promise 才能覆盖完整请求期间的防重复 |
| `onSelect` | `(item: QuoteItem) => void` | 打开详情由业务容器实现 |
| `onToggleWatch` | `(item: QuoteItem) => void \| Promise<void>` | 成功后业务容器更新 watchedIds；组件不会提前显示保存成功 |
| `colorConvention` | `'cn' \| 'international'`，`cn` | 红涨绿跌 / 绿涨红跌；文字方向保持一致 |
| `emptyAction` | `ReactNode` | 空观察池的添加入口 |
| `className` | `string` | 容器样式扩展 |

## 使用示例

```tsx
import { useState } from 'react';
import { QuoteItem, WatchlistPanel } from './components/market-ui';

function MyWatchlist({ quotes, reload }: {
  quotes: readonly QuoteItem[];
  reload: () => Promise<void>;
}) {
  const [selectedId, setSelectedId] = useState<string>();
  const [watchedIds, setWatchedIds] = useState<readonly string[]>([]);
  return (
    <WatchlistPanel
      items={quotes}
      selectedId={selectedId}
      watchedIds={watchedIds}
      onRefresh={reload}
      onSelect={item => setSelectedId(item.id)}
      onToggleWatch={async item => {
        // await saveWatchState(item.id); // 先完成业务接口写入，再更新受控状态
        setWatchedIds(ids => ids.includes(item.id)
          ? ids.filter(id => id !== item.id) : [...ids, item.id]);
      }}
    />
  );
}
```

现有 `marketService.MarketQuote` 可映射 `price → price`、`change_percent → changePercent`、`provider_name → source`。`updatedAt` 应使用明确带时区的行情时间；只有抓取时间时，在 `delayNote` 中说明时间代表接口抓取。`Stock` 可映射 `currentPrice / changePercent / quoteProviderName / quoteMarketTime`。数据服务若以零作为“未获取”的哨兵，适配器应根据可用性将其转为 `null`，组件不会猜测。

`quality` 必须由数据容器根据真实来源声明：mock 优先标记演示；实时授权与数据质量确认为实时才用 live；延迟或缓存分别用 delayed / stale；缺失用 unavailable。近期时间戳本身不能证明实时。

## 状态、响应式与可访问性

- 首次加载使用三张骨架卡片；刷新保留旧卡片，并把仍声明 live 的旧报价暂标为缓存行情。失败保留旧数据与失败提示；延迟和演示标签保持原语义。
- 内部刷新失败按标的 ID 记录旧报价，只降级仍未验证更新的旧实时报价，新增报价不继承旧失败。手动成功重试会解除内部错误；外部更新中，失败快照的标的均被移除或获得可验证的更新行情时间，也会解除内部错误。无有效时间的外部更新请显式通过成功重试或受控数据容器处理。
- 两个刷新入口共享请求锁。每个标的的监控锁保留在面板内，筛选或排序不会绕过正在执行的请求。
- 无数据与搜索无结果分别提供文案；搜索忽略首尾空格和大小写。缺失涨跌幅在升降排序中始终置后，零值正常参与排序。
- Grid 根据可用宽度自动排成一至多列；工具栏可换行，长名称可换行；所有交互控件最小高度 44px。没有只在 hover 下出现的功能。
- 原生按钮、search、select 支持键盘；选中行情用 `aria-current`，监控用 `aria-pressed`；区域、字段、卡片均有名称；忙碌、错误和筛选结果提供读屏状态。加载播报位于 `aria-busy` 区域之外。
- 涨跌含箭头、符号和读屏文字；人民币、港元和美元使用明确币种代码；深浅主题沿用全局令牌，支持减少动态效果和强制颜色模式。

## 正式业务接入

已接入 daocaijing.com 终端的“我的股票”行情列表：`QuoteValue` 负责报价与涨跌幅，`QuoteStatus` 标注延迟、缓存、演示和不可用状态；`TerminalQuoteName` 提供原生键盘选择。分组、排序、日内区间、成交量和基本面仍由原终端管理。正式终端沿用绿涨红跌；选中标的后展开来源与时间说明。

适配器为 `terminal/terminalQuoteDisplay.ts`，只使用真实报价元数据。缺少有效行情时间时，可展示真实接口获取时间，并明确说明两者区别；漏返标的立即标注缓存，不用本地抓取时刻制造交易所实时性。手机采用独立数值列宽，并将代码与名称分行，避免千位分隔符使报价截断。

## 可运行示例与验证

运行 `npm start`，打开 `/dev/ui`；组件工作台提供有数据、首次加载、空数据、加载失败、保留旧数据，以及模拟刷新失败。示例页在开发模式下可用，生产构建不提供这个入口；所有演示价格明确标注为样例。

源代码：`examples/MarketUIShowcase.tsx`。

```bash
CI=true npm test -- --watchAll=false --runInBand --testPathPattern='market(Quote|Watchlist)'
npm run lint
npm run build
```

两个测试文件覆盖报价、质量状态、时区、异步防重、键盘选择、监控状态、刷新失败恢复、过滤和排序。业务接入后仍需由业务容器负责真实 API 合约、账户切换取消请求和报价过期判定。
