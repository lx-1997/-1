import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  message,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography
} from 'antd';
import {
  ArrowRightOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  LinkOutlined,
  PartitionOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from 'recharts';
import { AppState } from '../types';
import {
  AiSupplyChainCapacityTrends,
  AiSupplyChainHorizon,
  CapacityTrendPoint,
  DeliveryTrendPoint,
  PricingTrendPoint,
  fetchAiSupplyChainCapacityTrends
} from '../services/aiSupplyChainService';

const { Paragraph, Text, Title } = Typography;

type SupplyLayer = 'upstream' | 'chip' | 'server' | 'infra';
type Trend = 'tightening' | 'stable' | 'easing';
type Confidence = 'high' | 'medium' | 'low';
type DataTier = 'hard' | 'industry' | 'model';
type FactorReadiness = 'direct' | 'confirm' | 'watch';
type CapacitySourceType = 'official' | 'company' | 'industry' | 'proxy';
type CapacityPressure = 'overheated' | 'tight' | 'balanced' | 'soft';

interface SupplyNode {
  id: string;
  layer: SupplyLayer;
  title: string;
  subtitle: string;
  leadMinWeeks: number;
  leadMaxWeeks: number;
  displayLead?: string;
  bottleneckScore: number;
  trend: Trend;
  confidence: Confidence;
  dataTier: DataTier;
  factorReadiness: FactorReadiness;
  verifiedAt: string;
  updateCadence: string;
  source: string;
  sourceUrl: string;
  sourceNote: string;
  factorNote: string;
  watchSymbols: string[];
}

interface SupplyEdge {
  from: string;
  to: string;
  label: string;
  criticality: number;
}

interface CapacityUtilizationMetric {
  id: string;
  segment: string;
  layer: SupplyLayer;
  reportedUtilization?: number;
  estimateLow?: number;
  estimateHigh?: number;
  pressure: CapacityPressure;
  sourceType: CapacitySourceType;
  source: string;
  sourceUrl: string;
  sourceDate: string;
  cadence: string;
  readout: string;
  agentUse: string;
}

interface AiSupplyChainCycleCenterProps {
  appState: AppState;
}

const layerMeta: Record<SupplyLayer, { label: string; description: string; color: string }> = {
  upstream: {
    label: '上游材料/晶圆',
    description: '长周期原料、晶圆制造、关键材料',
    color: '#167c80'
  },
  chip: {
    label: '芯片/封装',
    description: 'HBM、CoWoS、先进封装和基板',
    color: '#6d5bd0'
  },
  server: {
    label: '服务器/网络',
    description: 'AI加速卡、服务器板卡、光互联',
    color: '#b45309'
  },
  infra: {
    label: '数据中心交付',
    description: '电力设备、开关柜、机房设备',
    color: '#166534'
  }
};

const trendMeta: Record<Trend, { label: string; color: string; text: string }> = {
  tightening: { label: '趋紧', color: 'red', text: '交付周期上行或供给被锁定' },
  stable: { label: '平稳', color: 'blue', text: '交付周期大致稳定' },
  easing: { label: '缓和', color: 'green', text: '供给约束边际减轻' }
};

const confidenceMeta: Record<Confidence, { label: string; color: string }> = {
  high: { label: '高', color: 'green' },
  medium: { label: '中', color: 'gold' },
  low: { label: '低', color: 'default' }
};

const dataTierMeta: Record<DataTier, { label: string; color: string; description: string }> = {
  hard: {
    label: '硬数据',
    color: 'green',
    description: '来自机构报告、公司披露或明确公开口径，可直接进入因子但仍需定期复核。'
  },
  industry: {
    label: '行业估算',
    color: 'gold',
    description: '来自产业链公开材料、行业媒体或供应链评论，适合降权使用。'
  },
  model: {
    label: '模型推断',
    color: 'default',
    description: '由价格、订单、新闻、库存、股价等代理变量推断，只能做观察项。'
  }
};

const readinessMeta: Record<FactorReadiness, { label: string; color: string; description: string }> = {
  direct: {
    label: '可直接入因子',
    color: 'green',
    description: '数字口径相对稳定，可进入供需分或瓶颈分计算。'
  },
  confirm: {
    label: '需交叉验证',
    color: 'gold',
    description: '需要用订单、价格、公司披露或第二来源确认后再加权。'
  },
  watch: {
    label: '只观察',
    color: 'default',
    description: '适合提示方向，不适合直接影响仓位。'
  }
};

const capacitySourceMeta: Record<CapacitySourceType, { label: string; color: string }> = {
  official: { label: '官方统计', color: 'green' },
  company: { label: '公司披露', color: 'blue' },
  industry: { label: '产业调研', color: 'gold' },
  proxy: { label: '代理模型', color: 'default' }
};

const capacityPressureMeta: Record<CapacityPressure, { label: string; color: string; score: number }> = {
  overheated: { label: '满载/超配', color: 'red', score: 96 },
  tight: { label: '偏紧', color: 'orange', score: 86 },
  balanced: { label: '均衡', color: 'blue', score: 72 },
  soft: { label: '偏松', color: 'green', score: 58 }
};

const capacityMetrics: CapacityUtilizationMetric[] = [
  {
    id: 'us-electronics-baseline',
    segment: '美国电脑与电子产品',
    layer: 'upstream',
    reportedUtilization: 74.8,
    pressure: 'balanced',
    sourceType: 'official',
    source: 'FRED / Federal Reserve G.17, NAICS 334',
    sourceUrl: 'https://fred.stlouisfed.org/series/CAPUTLG334SQ',
    sourceDate: '2026 Q1',
    cadence: '月度/季度',
    readout: '官方宏观基线，用来判断电子制造大盘是否过热。',
    agentUse: '作为AI链利用率的下限锚，不直接代表HBM或CoWoS。'
  },
  {
    id: 'us-semiconductor-baseline',
    segment: '美国半导体及电子元件',
    layer: 'chip',
    reportedUtilization: 73.1,
    pressure: 'balanced',
    sourceType: 'official',
    source: 'FRED / Federal Reserve G.17, NAICS 3344',
    sourceUrl: 'https://fred.stlouisfed.org/series/CAPUTLG3344SQ',
    sourceDate: '2026 Q1',
    cadence: '月度/季度',
    readout: '官方半导体行业基线，覆盖面宽，不能细分到先进节点。',
    agentUse: '校准半导体大盘周期，避免把AI局部紧缺外推到全行业。'
  },
  {
    id: 'cn-electronics-baseline',
    segment: '中国计算机通信电子设备',
    layer: 'server',
    reportedUtilization: 75.4,
    pressure: 'balanced',
    sourceType: 'official',
    source: '国家统计局工业产能利用率',
    sourceUrl: 'https://www.stats.gov.cn/english/PressRelease/202604/t20260417_1963348.html',
    sourceDate: '2026 Q1',
    cadence: '季度',
    readout: '中国电子制造大盘利用率，适合对比A股硬件链需求弹性。',
    agentUse: '作为A股电子制造底层景气基线，不替代光模块或服务器ODM。'
  },
  {
    id: 'leading-edge-foundry',
    segment: '先进制程晶圆',
    layer: 'upstream',
    estimateLow: 90,
    estimateHigh: 98,
    pressure: 'tight',
    sourceType: 'company',
    source: 'TSMC 1Q26 Transcript',
    sourceUrl: 'https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-04/3cef85204275f94fd111485cfdf4adb3c0263c45/TSMC%201Q26%20Transcript.pdf',
    sourceDate: '2026-04-16',
    cadence: '季度',
    readout: 'AI相关领先制程需求强，公司披露整体利用率高于预期。',
    agentUse: '作为GPU/ASIC上游紧缺确认项，需结合资本开支和先进节点收入。'
  },
  {
    id: 'cowos-packaging',
    segment: 'CoWoS/2.5D先进封装',
    layer: 'chip',
    estimateLow: 95,
    estimateHigh: 100,
    pressure: 'overheated',
    sourceType: 'industry',
    source: 'TrendForce advanced packaging tracker',
    sourceUrl: 'https://www.trendforce.com/presscenter/news/20260430-13028.html',
    sourceDate: '2026-04-30',
    cadence: '月度',
    readout: '全球2.5D先进封装仍严重短缺，产能扩张前仍是算力链闸门。',
    agentUse: '作为AI链最高权重瓶颈，优先影响封测、载板、设备和GPU交付判断。'
  },
  {
    id: 'hbm-memory',
    segment: 'HBM/高端DRAM',
    layer: 'chip',
    estimateLow: 95,
    estimateHigh: 100,
    pressure: 'overheated',
    sourceType: 'company',
    source: 'SK hynix 1Q26 results',
    sourceUrl: 'https://news.skhynix.com/q1-2026-business-results/',
    sourceDate: '2026-04-22',
    cadence: '季度',
    readout: 'HBM与高容量服务器DRAM由AI需求拉动，客户需求超过供应能力。',
    agentUse: '作为存储链涨价和供应分配信号，需跟踪HBM合约、毛利率和扩产节奏。'
  },
  {
    id: 'ai-server-odm',
    segment: 'AI服务器ODM/整机',
    layer: 'server',
    estimateLow: 82,
    estimateHigh: 92,
    pressure: 'tight',
    sourceType: 'proxy',
    source: '台湾MOPS月营收 + ODM法说会',
    sourceUrl: 'https://mops.twse.com.tw/mops/web/t05st10_ifrs',
    sourceDate: '月度滚动',
    cadence: '月度',
    readout: '整机厂利用率很少直接披露，月营收、积压订单、零部件齐套率更实用。',
    agentUse: '作为下游兑现确认项，和GPU/HBM/光模块交期一起使用。'
  },
  {
    id: 'optical-networking',
    segment: '800G/1.6T光模块',
    layer: 'server',
    estimateLow: 85,
    estimateHigh: 95,
    pressure: 'tight',
    sourceType: 'proxy',
    source: '公司季报/订单 + LightCounting/Omdia类产业源',
    sourceUrl: 'https://www.lightcounting.com/',
    sourceDate: '月度/季度',
    cadence: '月度',
    readout: '光模块利用率多来自公司披露和行业调研，订单能见度比单点产能更可靠。',
    agentUse: '作为AI集群互联需求确认项，和价格、毛利率、客户集中度交叉验证。'
  },
  {
    id: 'power-equipment',
    segment: '数据中心电力设备',
    layer: 'infra',
    estimateLow: 80,
    estimateHigh: 95,
    pressure: 'tight',
    sourceType: 'industry',
    source: 'JLL/CBRE数据中心报告 + 电力设备公司订单',
    sourceUrl: 'https://www.jll.com/en-us/insights/global-data-center-outlook',
    sourceDate: '季度/半年',
    cadence: '季度',
    readout: '配电、开关柜、变压器通常披露交期和订单，不披露统一利用率。',
    agentUse: '作为AI基建落地约束项，连接算力需求与电力设备股票弹性。'
  }
];

const sourceDisclosureRows = [
  {
    title: '官方产能利用率',
    status: '真实官方数据',
    color: 'green',
    note: '优先在线读取 FRED / Federal Reserve G.17 月度序列；月度值按周展示只是承接到对应月份，不代表官方周频。',
    links: [
      { label: 'FRED 电脑与电子产品', url: 'https://fred.stlouisfed.org/series/CAPUTLG334S' },
      { label: 'FRED 半导体元件', url: 'https://fred.stlouisfed.org/series/CAPUTLG3344S' },
      { label: 'Fed G.17 表格', url: 'https://www.federalreserve.gov/releases/g17/current/table2_sup.htm' }
    ]
  },
  {
    title: '中国电子制造基线',
    status: '真实官方数据',
    color: 'green',
    note: '来自国家统计局季度工业产能利用率，用作中国电子制造大盘锚，不代表单一 AI 服务器或光模块产线。',
    links: [
      { label: '国家统计局', url: 'https://www.stats.gov.cn/english/PressRelease/202604/t20260417_1963348.html' }
    ]
  },
  {
    title: '产业新闻与研报信号',
    status: '真实公开网页',
    color: 'purple',
    note: '在线抓 TrendForce AI/HBM/Server 页面最新标题和日期，作为 5 月信号来源；标题是真实抓取，数值化是模型化处理。',
    links: [
      { label: 'TrendForce AI/HBM/Server', url: 'https://www.trendforce.com/research/category/Semiconductors/AI%20Server_HBM_Server' },
      { label: 'TrendForce CoWoS/3nm', url: 'https://www.trendforce.com/presscenter/news/20260430-13028.html' },
      { label: 'TrendForce 价格调查', url: 'https://www.trendforce.com/presscenter/news/20260331-12995.html' }
    ]
  },
  {
    title: '公司披露与行业锚点',
    status: '公开披露',
    color: 'blue',
    note: 'TSMC、SK hynix、JLL、MOPS 等用于校准先进制程、HBM、ODM、数据中心设备方向，不等同于统一周频数据库。',
    links: [
      { label: 'TSMC 法说会', url: 'https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-04/3cef85204275f94fd111485cfdf4adb3c0263c45/TSMC%201Q26%20Transcript.pdf' },
      { label: 'SK hynix 业绩', url: 'https://news.skhynix.com/q1-2026-business-results/' },
      { label: 'JLL 数据中心', url: 'https://www.jll.com/en-us/insights/global-data-center-outlook' }
    ]
  },
  {
    title: '交付/报价指数',
    status: '代理估算',
    color: 'orange',
    note: '交期周数和报价指数不是供应商逐笔报价，也不是官方统计。它把公开涨价区间、锁产、交期拉长、订单/营收信号映射成可比较的趋势。',
    links: [
      { label: 'LightCounting', url: 'https://www.lightcounting.com/' },
      { label: '台湾 MOPS', url: 'https://mops.twse.com.tw/mops/web/t05st10_ifrs' },
      { label: 'JLL Data Center Outlook', url: 'https://www.jll.com/content/dam/jllcom/en/global/documents/reports/research-reports/26-research-global-data-center-outlook-new.pdf' }
    ]
  }
];

const supplyNodes: SupplyNode[] = [
  {
    id: 'logic-wafer',
    layer: 'upstream',
    title: '先进制程晶圆',
    subtitle: 'GPU/ASIC die流片到出货节奏',
    leadMinWeeks: 14,
    leadMaxWeeks: 18,
    displayLead: '约16周',
    bottleneckScore: 72,
    trend: 'stable',
    confidence: 'medium',
    dataTier: 'hard',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '季度复核',
    source: 'Epoch AI',
    sourceUrl: 'https://epoch.ai/data-insights/ai-chip-supply-chain-constraints',
    sourceNote: '公开拆解口径显示，逻辑die通常在芯片出货前约16周完成制造。',
    factorNote: '晶圆周期决定上游节奏，但当前瓶颈更多在先进封装和电力交付。',
    watchSymbols: ['中芯国际', '北方华创', '中微公司']
  },
  {
    id: 'hbm',
    layer: 'chip',
    title: 'HBM3e/HBM4',
    subtitle: '高带宽内存锁产和备货',
    leadMinWeeks: 8,
    leadMaxWeeks: 52,
    displayLead: '>=8周, 锁产可达全年',
    bottleneckScore: 92,
    trend: 'tightening',
    confidence: 'medium',
    dataTier: 'industry',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'Epoch AI / TrendForce',
    sourceUrl: 'https://www.trendforce.com/presscenter/news/20260318-13001.html',
    sourceNote: '公开资料显示HBM需提前进入封装节奏，且头部客户锁定长期产能。',
    factorNote: 'HBM越紧，越利好存储链和封装链，但会压制下游整机交付弹性。',
    watchSymbols: ['香农芯创', '深科技', '兆易创新']
  },
  {
    id: 'cowos',
    layer: 'chip',
    title: 'CoWoS/先进封装',
    subtitle: '2.5D封装、interposer、先进封装产能',
    leadMinWeeks: 26,
    leadMaxWeeks: 52,
    displayLead: '6-12个月',
    bottleneckScore: 96,
    trend: 'tightening',
    confidence: 'medium',
    dataTier: 'industry',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'TrendForce / Epoch AI',
    sourceUrl: 'https://www.trendforce.com/presscenter/news/20260430-13028.html',
    sourceNote: 'TrendForce指出2.5D先进封装仍严重短缺，Epoch AI也将CoWoS和HBM列为AI芯片供给瓶颈。',
    factorNote: '先进封装是AI算力链最关键的供给闸门，瓶颈分最高。',
    watchSymbols: ['长电科技', '通富微电', '华天科技', 'ASMPT']
  },
  {
    id: 'substrate',
    layer: 'chip',
    title: '高端PCB/CCL/基板材料',
    subtitle: '高速板、覆铜板、ABF/载板相关材料',
    leadMinWeeks: 16,
    leadMaxWeeks: 20,
    displayLead: '16-20周',
    bottleneckScore: 76,
    trend: 'tightening',
    confidence: 'medium',
    dataTier: 'industry',
    factorReadiness: 'watch',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'Fusion Worldwide supply-chain note',
    sourceUrl: 'https://info.fusionww.com/hubfs/State%20of%20the%20Industry%20Reports/SOTI%20Reports/Fusion%20Worldwide%20Q1%202026%20State%20of%20the%20Industry%20Report.pdf',
    sourceNote: '电子材料公开供需跟踪中，CCL等材料常被列为16-20周量级。',
    factorNote: '如果PCB/CCL持续涨价并伴随交期拉长，A/H股PCB链弹性会提升。',
    watchSymbols: ['鹏鼎控股', '东山精密', '生益科技', '建滔积层板']
  },
  {
    id: 'pmic',
    layer: 'server',
    title: '服务器PMIC/电源芯片',
    subtitle: 'AI服务器主板电源管理',
    leadMinWeeks: 35,
    leadMaxWeeks: 40,
    displayLead: '35-40周',
    bottleneckScore: 82,
    trend: 'stable',
    confidence: 'medium',
    dataTier: 'hard',
    factorReadiness: 'direct',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'TrendForce',
    sourceUrl: 'https://www.trendforce.com/presscenter/news/20260415-13013.html',
    sourceNote: 'TrendForce曾跟踪服务器PMIC交期在35-40周量级，AI服务器需求会挤占高规格供给。',
    factorNote: 'PMIC紧张会放大服务器主板和整机交付不确定性。',
    watchSymbols: ['圣邦股份', '思瑞浦', '纳芯微']
  },
  {
    id: 'bmc',
    layer: 'server',
    title: 'BMC/管理芯片',
    subtitle: '服务器远程管理和板卡配套',
    leadMinWeeks: 21,
    leadMaxWeeks: 26,
    displayLead: '21-26周',
    bottleneckScore: 66,
    trend: 'stable',
    confidence: 'medium',
    dataTier: 'hard',
    factorReadiness: 'direct',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'TrendForce',
    sourceUrl: 'https://www.trendforce.com/presscenter/news/20260415-13013.html',
    sourceNote: '公开供应链跟踪中，服务器BMC交付周期曾处于21-26周区间。',
    factorNote: 'BMC不是最大瓶颈，但能作为服务器链订单兑现的确认项。',
    watchSymbols: ['工业富联', '浪潮信息', '紫光股份']
  },
  {
    id: 'optical',
    layer: 'server',
    title: '光模块/光纤连接',
    subtitle: '800G/1.6T光模块、光纤和互联',
    leadMinWeeks: 20,
    leadMaxWeeks: 52,
    displayLead: '20-52周',
    bottleneckScore: 88,
    trend: 'tightening',
    confidence: 'medium',
    dataTier: 'industry',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'Tom’s Hardware / DCD citation',
    sourceUrl: 'https://www.tomshardware.com/tech-industry/ai-data-centers-are-consuming-fiber-optic-cable-faster-than-suppliers-can-make-it',
    sourceNote: 'AI数据中心建设带来光纤需求上行，公开报道提到交期从20周拉长到52周。',
    factorNote: '光互联是AI集群扩容的第二关键链条，交期越长越利好高端光模块景气。',
    watchSymbols: ['中际旭创', '新易盛', '天孚通信', '长飞光纤光缆']
  },
  {
    id: 'dc-equipment',
    layer: 'infra',
    title: '数据中心设备平均交付',
    subtitle: '机房设备、制冷、配电综合交付',
    leadMinWeeks: 33,
    leadMaxWeeks: 42,
    displayLead: '33周全球/42周美国',
    bottleneckScore: 78,
    trend: 'tightening',
    confidence: 'high',
    dataTier: 'hard',
    factorReadiness: 'direct',
    verifiedAt: '2026-05-18',
    updateCadence: '季度复核',
    source: 'JLL Data Center Outlook',
    sourceUrl: 'https://www.jll.com/content/dam/jllcom/en/global/documents/reports/research-reports/26-research-global-data-center-outlook-new.pdf',
    sourceNote: 'JLL跟踪数据中心设备平均交付周期，全球约33周，美国约42周。',
    factorNote: '数据中心设备交付周期上行，会提高电力和温控链的策略权重。',
    watchSymbols: ['英维克', '申菱环境', '科华数据']
  },
  {
    id: 'switchgear',
    layer: 'infra',
    title: '开关柜/配电设备',
    subtitle: '中压开关柜、PDU、UPS配套',
    leadMinWeeks: 40,
    leadMaxWeeks: 65,
    displayLead: '40-65周',
    bottleneckScore: 84,
    trend: 'tightening',
    confidence: 'medium',
    dataTier: 'industry',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'Build data-center substation monitor',
    sourceUrl: 'https://build.inc/insights/data-center-substation-procurement-ai',
    sourceNote: '电力设备长交付正在成为AI数据中心建设约束，配电设备通常也处在长周期。',
    factorNote: '配电设备紧张会把AI算力景气传导到电力设备股。',
    watchSymbols: ['思源电气', '许继电气', '平高电气']
  },
  {
    id: 'transformer',
    layer: 'infra',
    title: '大型变压器',
    subtitle: '电网接入、园区主变和升压设备',
    leadMinWeeks: 60,
    leadMaxWeeks: 120,
    displayLead: '60-120周',
    bottleneckScore: 98,
    trend: 'tightening',
    confidence: 'high',
    dataTier: 'industry',
    factorReadiness: 'confirm',
    verifiedAt: '2026-05-18',
    updateCadence: '月度复核',
    source: 'Build data-center substation monitor',
    sourceUrl: 'https://build.inc/insights/data-center-substation-procurement-ai',
    sourceNote: '公开电力设备跟踪显示，大型变压器交付可达60-120周，是数据中心上线的长周期瓶颈。',
    factorNote: '变压器是最长周期因子，决定AI基建从订单到通电的最终速度。',
    watchSymbols: ['特变电工', '金盘科技', '中国西电']
  }
];

const supplyEdges: SupplyEdge[] = [
  { from: 'logic-wafer', to: 'cowos', label: '逻辑die进入先进封装', criticality: 82 },
  { from: 'hbm', to: 'cowos', label: 'HBM与GPU在CoWoS合封', criticality: 94 },
  { from: 'substrate', to: 'cowos', label: '基板/CCL限制封装良率和放量', criticality: 78 },
  { from: 'cowos', to: 'pmic', label: 'AI加速卡带动服务器板级配套', criticality: 72 },
  { from: 'pmic', to: 'bmc', label: '主板电源和管理芯片共同决定整机齐套', criticality: 64 },
  { from: 'cowos', to: 'optical', label: 'GPU集群扩大带动光互联', criticality: 88 },
  { from: 'optical', to: 'dc-equipment', label: '网络布线与机柜交付同步', criticality: 74 },
  { from: 'dc-equipment', to: 'switchgear', label: '机房设备受配电影响', criticality: 79 },
  { from: 'switchgear', to: 'transformer', label: '配电和主变决定通电窗口', criticality: 90 }
];

const layerOrder: SupplyLayer[] = ['upstream', 'chip', 'server', 'infra'];

const formatLead = (node: SupplyNode): string => node.displayLead || `${node.leadMinWeeks}-${node.leadMaxWeeks}周`;

const leadMidpoint = (node: SupplyNode): number => Math.round((node.leadMinWeeks + node.leadMaxWeeks) / 2);

const capacityDisplay = (metric: CapacityUtilizationMetric): string => (
  typeof metric.reportedUtilization === 'number'
    ? `${metric.reportedUtilization.toFixed(1)}%`
    : `${metric.estimateLow}-${metric.estimateHigh}%`
);

const capacityPoint = (metric: CapacityUtilizationMetric): number => (
  typeof metric.reportedUtilization === 'number'
    ? metric.reportedUtilization
    : Math.round(((metric.estimateLow || 0) + (metric.estimateHigh || 0)) / 2)
);

const capacityUtilizationTrend: CapacityTrendPoint[] = [
  { week: '02/23', electronics: 74.7, semiconductor: 72.9, aiProxy: 90.6 },
  { week: '03/02', electronics: 74.7, semiconductor: 72.2, aiProxy: 90.9 },
  { week: '03/09', electronics: 74.7, semiconductor: 72.2, aiProxy: 91.2 },
  { week: '03/16', electronics: 74.7, semiconductor: 72.2, aiProxy: 91.5 },
  { week: '03/23', electronics: 74.7, semiconductor: 72.2, aiProxy: 91.8 },
  { week: '03/30', electronics: 74.7, semiconductor: 72.2, aiProxy: 92.0 },
  { week: '04/06', electronics: 75.4, semiconductor: 72.2, aiProxy: 92.1 },
  { week: '04/13', electronics: 75.4, semiconductor: 72.2, aiProxy: 92.3 },
  { week: '04/20', electronics: 75.4, semiconductor: 72.2, aiProxy: 92.5 },
  { week: '04/27', electronics: 75.4, semiconductor: 72.2, aiProxy: 92.6 },
  { week: '05/04', electronics: null, semiconductor: null, aiProxy: null },
  { week: '05/11', electronics: null, semiconductor: null, aiProxy: null },
  { week: '05/18', electronics: null, semiconductor: null, aiProxy: null }
];

const deliveryLeadTrend: DeliveryTrendPoint[] = [
  { week: '02/23', cowos: 44, hbm: 44, optical: 36, power: 58, wafer: 28, substrate: 34, pcb: 26, ssd: 20, rack: 28 },
  { week: '03/02', cowos: 45, hbm: 45, optical: 37, power: 59, wafer: 29, substrate: 35, pcb: 27, ssd: 21, rack: 29 },
  { week: '03/09', cowos: 46, hbm: 46, optical: 39, power: 60, wafer: 30, substrate: 36, pcb: 28, ssd: 22, rack: 30 },
  { week: '03/16', cowos: 47, hbm: 48, optical: 41, power: 61, wafer: 31, substrate: 37, pcb: 29, ssd: 23, rack: 31 },
  { week: '03/23', cowos: 48, hbm: 49, optical: 43, power: 62, wafer: 32, substrate: 38, pcb: 30, ssd: 24, rack: 32 },
  { week: '03/30', cowos: 48, hbm: 50, optical: 44, power: 62, wafer: 32, substrate: 39, pcb: 31, ssd: 25, rack: 33 },
  { week: '04/06', cowos: 49, hbm: 50, optical: 46, power: 63, wafer: 33, substrate: 40, pcb: 32, ssd: 26, rack: 34 },
  { week: '04/13', cowos: 50, hbm: 51, optical: 48, power: 63, wafer: 33, substrate: 41, pcb: 33, ssd: 27, rack: 35 },
  { week: '04/20', cowos: 51, hbm: 51, optical: 50, power: 64, wafer: 34, substrate: 42, pcb: 34, ssd: 28, rack: 36 },
  { week: '04/27', cowos: 52, hbm: 52, optical: 52, power: 65, wafer: 34, substrate: 43, pcb: 35, ssd: 29, rack: 37 },
  { week: '05/04', cowos: null, hbm: null, optical: null, power: null },
  { week: '05/11', cowos: null, hbm: null, optical: null, power: null },
  { week: '05/18', cowos: null, hbm: null, optical: null, power: null }
];

type DeliverySeriesKey = keyof Pick<DeliveryTrendPoint, 'cowos' | 'hbm' | 'optical' | 'power' | 'wafer' | 'substrate' | 'pcb' | 'ssd' | 'rack'>;

const deliverySeries: Array<{ key: DeliverySeriesKey; name: string; color: string; core?: boolean }> = [
  { key: 'cowos', name: 'CoWoS', color: '#dc2626', core: true },
  { key: 'hbm', name: 'HBM', color: '#6d5bd0', core: true },
  { key: 'optical', name: '光互联', color: '#b45309', core: true },
  { key: 'power', name: '电力设备', color: '#166534', core: true },
  { key: 'wafer', name: '先进制程', color: '#0f766e' },
  { key: 'substrate', name: 'ABF/载板', color: '#7c3aed' },
  { key: 'pcb', name: 'PCB/T-glass', color: '#be123c' },
  { key: 'ssd', name: 'SSD/NAND', color: '#2563eb' },
  { key: 'rack', name: '机柜整机', color: '#4d7c0f' }
];

const pricingTrendFallback: PricingTrendPoint[] = [
  { week: '02/23', hbmDram: 100, enterpriseSsd: 100, cowosPackaging: 100, substratePcb: 100, powerIc: 100, opticalModule: 100, rackBom: 100 },
  { week: '03/02', hbmDram: 104, enterpriseSsd: 105, cowosPackaging: 101, substratePcb: 102, powerIc: 101, opticalModule: 101, rackBom: 101 },
  { week: '03/09', hbmDram: 108, enterpriseSsd: 111, cowosPackaging: 102, substratePcb: 104, powerIc: 102, opticalModule: 102, rackBom: 103 },
  { week: '03/16', hbmDram: 113, enterpriseSsd: 118, cowosPackaging: 104, substratePcb: 107, powerIc: 104, opticalModule: 103, rackBom: 105 },
  { week: '03/23', hbmDram: 119, enterpriseSsd: 126, cowosPackaging: 106, substratePcb: 110, powerIc: 106, opticalModule: 104, rackBom: 107 },
  { week: '03/30', hbmDram: 126, enterpriseSsd: 135, cowosPackaging: 108, substratePcb: 113, powerIc: 108, opticalModule: 105, rackBom: 109 },
  { week: '04/06', hbmDram: 132, enterpriseSsd: 143, cowosPackaging: 110, substratePcb: 116, powerIc: 110, opticalModule: 106, rackBom: 111 },
  { week: '04/13', hbmDram: 138, enterpriseSsd: 151, cowosPackaging: 112, substratePcb: 118, powerIc: 111, opticalModule: 107, rackBom: 113 },
  { week: '04/20', hbmDram: 144, enterpriseSsd: 158, cowosPackaging: 114, substratePcb: 120, powerIc: 112, opticalModule: 108, rackBom: 114 },
  { week: '04/27', hbmDram: 149, enterpriseSsd: 164, cowosPackaging: 116, substratePcb: 122, powerIc: 113, opticalModule: 109, rackBom: 116 },
  { week: '05/04', hbmDram: 152, enterpriseSsd: 169, cowosPackaging: 117, substratePcb: 124, powerIc: 115, opticalModule: 110, rackBom: 118, signal: true },
  { week: '05/11', hbmDram: 155, enterpriseSsd: 173, cowosPackaging: 118, substratePcb: 125, powerIc: 116, opticalModule: 111, rackBom: 119, signal: true },
  { week: '05/18', hbmDram: 157, enterpriseSsd: 176, cowosPackaging: 119, substratePcb: 126, powerIc: 117, opticalModule: 112, rackBom: 120, signal: true }
];

type PricingSeriesKey = keyof Pick<PricingTrendPoint, 'hbmDram' | 'enterpriseSsd' | 'cowosPackaging' | 'substratePcb' | 'powerIc' | 'opticalModule' | 'rackBom'>;

const pricingSeries: Array<{ key: PricingSeriesKey; name: string; color: string; core?: boolean }> = [
  { key: 'hbmDram', name: 'HBM/Server DRAM', color: '#dc2626', core: true },
  { key: 'enterpriseSsd', name: 'Enterprise SSD/NAND', color: '#2563eb', core: true },
  { key: 'cowosPackaging', name: 'CoWoS封装', color: '#7c3aed' },
  { key: 'substratePcb', name: 'ABF/PCB/T-glass', color: '#be123c' },
  { key: 'powerIc', name: '电源IC/8英寸', color: '#166534' },
  { key: 'opticalModule', name: '光模块', color: '#b45309' },
  { key: 'rackBom', name: '机柜BOM', color: '#4d7c0f' }
];

type TrendPoint = CapacityTrendPoint | DeliveryTrendPoint | PricingTrendPoint;

const firstObserved = <T extends TrendPoint>(items: T[], key: keyof T): number => {
  const found = items.find(item => typeof item[key] === 'number');
  return typeof found?.[key] === 'number' ? found[key] as number : 0;
};

const lastObserved = <T extends TrendPoint>(items: T[], key: keyof T): number => {
  const found = [...items].reverse().find(item => typeof item[key] === 'number');
  return typeof found?.[key] === 'number' ? found[key] as number : 0;
};

const pointHasNumericValue = (item: TrendPoint): boolean => (
  ['electronics', 'semiconductor', 'aiProxy', 'cowos', 'hbm', 'optical', 'power', 'wafer', 'substrate', 'pcb', 'ssd', 'rack', 'hbmDram', 'enterpriseSsd', 'cowosPackaging', 'substratePcb', 'powerIc', 'opticalModule', 'rackBom']
    .some(key => typeof (item as unknown as Record<string, unknown>)[key] === 'number')
);

const trendStaleRange = <T extends TrendPoint>(items: T[], fallbackStart?: string | null) => {
  const explicitStart = fallbackStart && items.some(item => item.week === fallbackStart) ? fallbackStart : null;
  const inferredStart = items.find(item => item.observed === false || !pointHasNumericValue(item))?.week || null;
  const start = explicitStart || inferredStart;
  const end = start ? items[items.length - 1]?.week || start : null;
  return start && end ? { start, end } : null;
};

const trendSignalRange = <T extends TrendPoint>(items: T[]) => {
  const start = items.find(item => item.signal)?.week || null;
  const end = start ? items[items.length - 1]?.week || start : null;
  return start && end ? { start, end } : null;
};

const trendPrefixGapRange = <T extends TrendPoint>(items: T[]) => {
  const firstNumericIndex = items.findIndex(pointHasNumericValue);
  if (firstNumericIndex <= 0) {
    return null;
  }
  return {
    start: items[0]?.week || '',
    end: items[firstNumericIndex - 1]?.week || items[0]?.week || ''
  };
};

const trendTailStaleRange = <T extends TrendPoint>(items: T[]) => {
  const lastNumericIndex = [...items].reverse().findIndex(pointHasNumericValue);
  if (lastNumericIndex < 0) {
    return items.length ? { start: items[0].week, end: items[items.length - 1].week } : null;
  }
  const resolvedLastNumericIndex = items.length - 1 - lastNumericIndex;
  if (resolvedLastNumericIndex >= items.length - 1) {
    return null;
  }
  return {
    start: items[resolvedLastNumericIndex + 1]?.week || items[items.length - 1]?.week || '',
    end: items[items.length - 1]?.week || ''
  };
};

const dataQualityRows = [
  {
    tier: 'hard' as DataTier,
    examples: 'TrendForce PMIC/BMC、JLL数据中心设备',
    usage: '可以进入瓶颈分和仓位闸门，权重正常'
  },
  {
    tier: 'industry' as DataTier,
    examples: 'CoWoS、HBM、光纤、变压器公开交期',
    usage: '进入模型前降权，并要求第二来源确认'
  },
  {
    tier: 'model' as DataTier,
    examples: '新闻热度、价格/库存代理、订单传闻',
    usage: '只做预警，不直接触发买卖'
  }
];

const AiSupplyChainCycleCenter: React.FC<AiSupplyChainCycleCenterProps> = ({ appState }) => {
  const [selectedId, setSelectedId] = useState('cowos');
  const [layerFilter, setLayerFilter] = useState<SupplyLayer | 'all'>('all');
  const [trendHorizon, setTrendHorizon] = useState<AiSupplyChainHorizon>('3m');
  const [liveTrends, setLiveTrends] = useState<AiSupplyChainCapacityTrends | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);

  const refreshLatestTrends = async (silent = false) => {
    setTrendLoading(true);
    try {
      const data = await fetchAiSupplyChainCapacityTrends(trendHorizon);
      setLiveTrends(data);
      if (!silent) {
        message.success(`已刷新AI供应链${trendHorizon === '1y' ? '最近一年' : '最近3个月'}观测`);
      }
    } catch (error: any) {
      if (!silent) {
        message.warning(error?.message || '最新数据刷新失败，继续使用内置观测');
      }
    } finally {
      setTrendLoading(false);
    }
  };

  useEffect(() => {
    refreshLatestTrends(true);
  }, [trendHorizon]);

  const selectedNode = useMemo(
    () => supplyNodes.find(node => node.id === selectedId) || supplyNodes[0],
    [selectedId]
  );

  const filteredNodes = useMemo(() => (
    layerFilter === 'all'
      ? supplyNodes
      : supplyNodes.filter(node => node.layer === layerFilter)
  ), [layerFilter]);

  const chartData = useMemo(() => (
    [...filteredNodes]
      .sort((a, b) => b.leadMaxWeeks - a.leadMaxWeeks)
      .map(node => ({
        name: node.title,
        mid: leadMidpoint(node),
        max: node.leadMaxWeeks,
        bottleneck: node.bottleneckScore
      }))
  ), [filteredNodes]);

  const selectedUpstream = supplyEdges.filter(edge => edge.to === selectedNode.id);
  const selectedDownstream = supplyEdges.filter(edge => edge.from === selectedNode.id);
  const criticalPath = [...supplyNodes].sort((a, b) => b.bottleneckScore - a.bottleneckScore).slice(0, 5);
  const hardDataCount = supplyNodes.filter(node => node.dataTier === 'hard').length;
  const directFactorCount = supplyNodes.filter(node => node.factorReadiness === 'direct').length;
  const visibleLayerOrder = layerFilter === 'all' ? layerOrder : [layerFilter];
  const officialCapacityMetrics = capacityMetrics.filter(metric => metric.sourceType === 'official');
  const aiCapacityMetrics = capacityMetrics.filter(metric => metric.sourceType !== 'official');
  const capacityTrend = liveTrends?.capacity_trend?.length ? liveTrends.capacity_trend : capacityUtilizationTrend;
  const deliveryTrend = liveTrends?.delivery_trend?.length ? liveTrends.delivery_trend : deliveryLeadTrend;
  const pricingTrend = liveTrends?.pricing_trend?.length ? liveTrends.pricing_trend : pricingTrendFallback;
  const capacityStaleRange = trendStaleRange(capacityTrend, liveTrends?.stale_from);
  const deliveryStaleRange = trendStaleRange(deliveryTrend, liveTrends?.stale_from);
  const pricingHistoryGapRange = trendPrefixGapRange(pricingTrend);
  const pricingStaleRange = trendTailStaleRange(pricingTrend);
  const capacitySignalRange = trendSignalRange(capacityTrend);
  const deliverySignalRange = trendSignalRange(deliveryTrend);
  const pricingSignalRange = trendSignalRange(pricingTrend);
  const trendTitlePrefix = trendHorizon === '1y' ? '最近一年' : '最近3个月';
  const xAxisInterval = trendHorizon === '1y' ? 7 : 2;
  const latestIndustryUpdates = liveTrends?.industry_updates?.slice(0, 4) || [];
  const fetchedAt = liveTrends?.fetched_at
    ? new Date(liveTrends.fetched_at).toLocaleString('zh-CN', { hour12: false })
    : null;
  const officialBaseline = Math.round(
    officialCapacityMetrics.reduce((sum, metric) => sum + capacityPoint(metric), 0) / officialCapacityMetrics.length * 10
  ) / 10;
  const aiProxyAverage = Math.round(
    aiCapacityMetrics.reduce((sum, metric) => sum + capacityPoint(metric), 0) / aiCapacityMetrics.length * 10
  ) / 10;
  const latestAiProxyPressure = lastObserved(capacityTrend, 'aiProxy') || aiProxyAverage;
  const electronicsTrendDelta = Math.round((lastObserved(capacityTrend, 'electronics') - firstObserved(capacityTrend, 'electronics')) * 10) / 10;
  const semiconductorTrendDelta = Math.round((lastObserved(capacityTrend, 'semiconductor') - firstObserved(capacityTrend, 'semiconductor')) * 10) / 10;
  const cowosLeadDelta = lastObserved(deliveryTrend, 'cowos') - firstObserved(deliveryTrend, 'cowos');
  const opticalLeadDelta = lastObserved(deliveryTrend, 'optical') - firstObserved(deliveryTrend, 'optical');
  const hbmPriceDelta = Math.round((lastObserved(pricingTrend, 'hbmDram') - firstObserved(pricingTrend, 'hbmDram')) * 10) / 10;
  const ssdPriceDelta = Math.round((lastObserved(pricingTrend, 'enterpriseSsd') - firstObserved(pricingTrend, 'enterpriseSsd')) * 10) / 10;
  const tightCapacityMetrics = [...capacityMetrics]
    .sort((a, b) => capacityPressureMeta[b.pressure].score - capacityPressureMeta[a.pressure].score)
    .slice(0, 4);
  const matchedWatchlist = appState.stocks.filter(stock => (
    supplyNodes.some(node => node.watchSymbols.some(symbol => stock.name.includes(symbol) || symbol.includes(stock.name)))
  ));

  useEffect(() => {
    if (layerFilter === 'all') {
      return;
    }
    if (selectedNode.layer !== layerFilter) {
      const firstNodeInLayer = supplyNodes.find(node => node.layer === layerFilter);
      if (firstNodeInLayer) {
        setSelectedId(firstNodeInLayer.id);
      }
    }
  }, [layerFilter, selectedNode.layer]);

  const tableColumns = [
    {
      title: '环节',
      key: 'node',
      render: (_: unknown, node: SupplyNode) => (
        <button type="button" className="ai-chain-table-node" onClick={() => setSelectedId(node.id)}>
          <strong>{node.title}</strong>
          <span>{layerMeta[node.layer].label} · {node.subtitle}</span>
        </button>
      )
    },
    {
      title: '交付周期',
      key: 'lead',
      width: 130,
      render: (_: unknown, node: SupplyNode) => <Text strong>{formatLead(node)}</Text>
    },
    {
      title: '瓶颈分',
      dataIndex: 'bottleneckScore',
      width: 130,
      render: (score: number) => <Progress percent={score} size="small" strokeColor={score >= 88 ? '#dc2626' : '#167c80'} />
    },
    {
      title: '趋势',
      key: 'trend',
      width: 90,
      render: (_: unknown, node: SupplyNode) => <Tag color={trendMeta[node.trend].color}>{trendMeta[node.trend].label}</Tag>
    },
    {
      title: '数据等级',
      key: 'dataTier',
      width: 130,
      render: (_: unknown, node: SupplyNode) => (
        <Space direction="vertical" size={0}>
          <Tag color={dataTierMeta[node.dataTier].color}>{dataTierMeta[node.dataTier].label}</Tag>
          <Text type="secondary" style={{ fontSize: 11 }}>{node.updateCadence}</Text>
        </Space>
      )
    }
  ];

  const capacityColumns = [
    {
      title: '环节',
      key: 'segment',
      render: (_: unknown, metric: CapacityUtilizationMetric) => (
        <Space direction="vertical" size={0}>
          <Text strong>{metric.segment}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>{layerMeta[metric.layer].label} · {metric.sourceDate}</Text>
        </Space>
      )
    },
    {
      title: '利用率/区间',
      key: 'utilization',
      width: 130,
      render: (_: unknown, metric: CapacityUtilizationMetric) => <Text strong>{capacityDisplay(metric)}</Text>
    },
    {
      title: '状态',
      key: 'pressure',
      width: 110,
      render: (_: unknown, metric: CapacityUtilizationMetric) => (
        <Tag color={capacityPressureMeta[metric.pressure].color}>{capacityPressureMeta[metric.pressure].label}</Tag>
      )
    },
    {
      title: '来源',
      key: 'source',
      width: 150,
      render: (_: unknown, metric: CapacityUtilizationMetric) => (
        <a href={metric.sourceUrl} target="_blank" rel="noreferrer">
          {capacitySourceMeta[metric.sourceType].label}
        </a>
      )
    }
  ];

  return (
    <div className="ai-supply-chain-shell">
      <div className="ai-supply-chain-header">
        <div>
          <Space size={8} align="center">
            <PartitionOutlined className="ai-supply-chain-title-icon" />
            <Title level={3}>AI 产业链交付周期地图</Title>
          </Space>
          <Paragraph className="section-description">
            用交付周期、瓶颈分和上下游依赖关系，追踪AI算力链从芯片到数据中心上线的供需约束。
          </Paragraph>
        </div>
        <Space wrap>
          <Tag color="red">最长瓶颈: 变压器 60-120周</Tag>
          <Tag color="purple">封装瓶颈: CoWoS 6-12个月</Tag>
          <Tag color="green">硬数据节点: {hardDataCount}/{supplyNodes.length}</Tag>
          <Tag color="blue">验证日: 2026-05-18</Tag>
        </Space>
      </div>

      <Alert
        className="ai-supply-chain-alert"
        type="info"
        showIcon
        message="交付周期是策略因子，不是采购承诺"
        description="板块把公开可得的交期、锁产和数据中心设备交付信息转成可视化因子。后续可以接新闻、财报、研报和公告，按日期自动更新每个节点的趋势。"
      />

      <section className="ai-capacity-module">
        <div className="ai-capacity-module-head">
          <div>
            <Space size={8}>
              <DatabaseOutlined />
              <Title level={4}>行业平均产能利用率</Title>
            </Space>
            <Paragraph>
              官方统计给大盘基线，公司披露和产业调研给AI链局部瓶颈；两者分层进入 Agent 证据。
            </Paragraph>
          </div>
          <Space wrap>
            <Tag color="green">官方基线 {officialBaseline}%</Tag>
            <Tag color="red">AI链代理压力 {latestAiProxyPressure}%</Tag>
            <Tag color="gold">直接口径 {officialCapacityMetrics.length} 项</Tag>
            {liveTrends?.official_observed_through && <Tag color="blue">官方至 {liveTrends.official_observed_through}</Tag>}
            {liveTrends?.official_release_date && <Tag color="cyan">Fed发布 {liveTrends.official_release_date}</Tag>}
            {liveTrends?.industry_observed_through && <Tag color="purple">产业信号至 {liveTrends.industry_observed_through}</Tag>}
            {liveTrends?.proxy_observed_through && <Tag>结构化至 {liveTrends.proxy_observed_through}</Tag>}
            {liveTrends?.pricing_observed_through && <Tag color="magenta">报价至 {liveTrends.pricing_observed_through}</Tag>}
            <Segmented
              size="small"
              value={trendHorizon}
              onChange={value => setTrendHorizon(value as AiSupplyChainHorizon)}
              options={[
                { label: '近3个月', value: '3m' },
                { label: '近1年', value: '1y' }
              ]}
            />
            <Button size="small" icon={<ReloadOutlined />} loading={trendLoading} onClick={() => refreshLatestTrends(false)}>
              刷新最新数据
            </Button>
          </Space>
        </div>
        {liveTrends?.warnings?.length ? (
          <Alert
            type="warning"
            showIcon
            message="最新数据部分使用兜底观测"
            description={liveTrends.warnings.slice(0, 2).join('；')}
          />
        ) : null}

        <div className="ai-capacity-grid">
          <div className="ai-capacity-score-panel">
            <span>AI链产能压力</span>
            <strong>{latestAiProxyPressure}%</strong>
            <Progress
              percent={Math.round(latestAiProxyPressure)}
              strokeColor={latestAiProxyPressure >= 90 ? '#dc2626' : '#167c80'}
              showInfo={false}
            />
            <p>这是代理均值，不是官方全行业利用率；用于识别局部供给约束。</p>
          </div>

          <div className="ai-capacity-source-rail">
            <strong>数据路径</strong>
            <div>
              <Tag color="green">官方</Tag>
              <span>Fed G.17、Census QPC、国家统计局，给行业均值和区域基线。</span>
            </div>
            <div>
              <Tag color="blue">公司</Tag>
              <span>TSMC、SK hynix、Micron、ODM、光模块和电力设备法说会。</span>
            </div>
            <div>
              <Tag color="gold">产业</Tag>
              <span>TrendForce、SEMI、Omdia/LightCounting、Digitimes、JLL/CBRE。</span>
            </div>
            <div>
              <Tag>代理</Tag>
              <span>月营收、订单积压、交期、涨价、库存和资本开支共同打分。</span>
            </div>
          </div>

          <div className="ai-capacity-hotspots">
            <strong>当前高压环节</strong>
            {tightCapacityMetrics.map(metric => (
              <a href={metric.sourceUrl} target="_blank" rel="noreferrer" key={metric.id}>
                <span>{metric.segment}</span>
                <em>{capacityDisplay(metric)}</em>
              </a>
            ))}
          </div>
        </div>

        <div className="ai-capacity-source-disclosure">
          <div className="ai-capacity-source-disclosure-head">
            <strong>数据来源与真实性</strong>
            <span>真实数据和代理估算分层展示；Agent 使用时会按这个等级降权。</span>
          </div>
          <div className="ai-capacity-source-disclosure-grid">
            {sourceDisclosureRows.map(row => (
              <div className="ai-capacity-source-disclosure-row" key={row.title}>
                <div>
                  <strong>{row.title}</strong>
                  <Tag color={row.color}>{row.status}</Tag>
                </div>
                <p>{row.note}</p>
                <Space size={[6, 6]} wrap>
                  {row.links.map(link => (
                    <a href={link.url} target="_blank" rel="noreferrer" key={link.url}>
                      {link.label}
                    </a>
                  ))}
                </Space>
              </div>
            ))}
          </div>
        </div>

        <div className="ai-capacity-trend-grid">
          <Card
            size="small"
            title={`${trendTitlePrefix}利用率趋势`}
            extra={<Tag color={electronicsTrendDelta >= 0 ? 'green' : 'red'}>电子 {electronicsTrendDelta >= 0 ? '+' : ''}{electronicsTrendDelta}pct</Tag>}
          >
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={capacityTrend} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="week" interval={xAxisInterval} tick={{ fontSize: 11 }} />
                <YAxis domain={[68, 96]} tick={{ fontSize: 11 }} unit="%" />
                {capacitySignalRange && (
                  <ReferenceArea x1={capacitySignalRange.start} x2={capacitySignalRange.end} fill="#f59e0b" fillOpacity={0.1} label={{ value: '信号估算', position: 'insideTopRight', fill: '#b45309', fontSize: 11 }} />
                )}
                {capacityStaleRange && (
                  <ReferenceArea x1={capacityStaleRange.start} x2={capacityStaleRange.end} fill="#94a3b8" fillOpacity={0.12} label={{ value: '待更新', position: 'insideTopRight', fill: '#64748b', fontSize: 11 }} />
                )}
                <RechartsTooltip formatter={(value: number | string, name: string) => [typeof value === 'number' ? `${value}%` : '-', name]} />
                <Legend />
                <Line type="monotone" dataKey="electronics" name="官方电子制造" stroke="#167c80" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                <Line type="monotone" dataKey="semiconductor" name="官方半导体元件" stroke="#6d5bd0" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                <Line type="monotone" dataKey="aiProxy" name="AI链代理压力" stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
            <div className="ai-capacity-trend-note">
              <Tag color="green">Fed G.17</Tag>
              <Tag color={semiconductorTrendDelta >= 0 ? 'green' : 'red'}>半导体 {semiconductorTrendDelta >= 0 ? '+' : ''}{semiconductorTrendDelta}pct</Tag>
              <span>{fetchedAt ? `刷新于 ${fetchedAt}；` : ''}官方线仍是Fed月度口径；AI链代理压力是把公开产业信号映射成可比较的估计值。</span>
            </div>
          </Card>

          <Card
            size="small"
            title={`${trendTitlePrefix}供应链交付趋势`}
            extra={<Tag color="red">CoWoS +{cowosLeadDelta}周</Tag>}
          >
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={deliveryTrend} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="week" interval={xAxisInterval} tick={{ fontSize: 11 }} />
                <YAxis domain={[18, 74]} tick={{ fontSize: 11 }} unit="周" />
                {deliverySignalRange && (
                  <ReferenceArea x1={deliverySignalRange.start} x2={deliverySignalRange.end} fill="#f59e0b" fillOpacity={0.1} label={{ value: '信号估算', position: 'insideTopRight', fill: '#b45309', fontSize: 11 }} />
                )}
                {deliveryStaleRange && (
                  <ReferenceArea x1={deliveryStaleRange.start} x2={deliveryStaleRange.end} fill="#94a3b8" fillOpacity={0.12} label={{ value: '待更新', position: 'insideTopRight', fill: '#64748b', fontSize: 11 }} />
                )}
                <RechartsTooltip formatter={(value: number | string, name: string) => [typeof value === 'number' ? `${value}周` : '-', name]} />
                <Legend />
                {deliverySeries.map(series => (
                  <Line
                    key={series.key}
                    type="monotone"
                    dataKey={series.key}
                    name={series.name}
                    stroke={series.color}
                    strokeWidth={series.core ? 2 : 1.6}
                    dot={{ r: series.core ? 3 : 2 }}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <div className="ai-capacity-trend-note">
              <Tag color="gold">代理趋势</Tag>
              <span>{trendHorizon === '1y' ? '一年视图早期为代理基线估计；' : '4/27 前为结构化交期观测；'}5月末段是信号估算，不是采购报价。新增先进制程、ABF/载板、PCB/T-glass、SSD/NAND、机柜整机。</span>
            </div>
          </Card>

          <Card
            size="small"
            title={`${trendTitlePrefix}AI供应链报价指数`}
            extra={<Tag color="red">HBM/DRAM +{hbmPriceDelta}</Tag>}
          >
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={pricingTrend} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="week" interval={xAxisInterval} tick={{ fontSize: 11 }} />
                <YAxis domain={[95, 182]} tick={{ fontSize: 11 }} />
                {pricingHistoryGapRange && (
                  <ReferenceArea x1={pricingHistoryGapRange.start} x2={pricingHistoryGapRange.end} fill="#94a3b8" fillOpacity={0.1} label={{ value: '无周频报价', position: 'insideTopLeft', fill: '#64748b', fontSize: 11 }} />
                )}
                {pricingSignalRange && (
                  <ReferenceArea x1={pricingSignalRange.start} x2={pricingSignalRange.end} fill="#f59e0b" fillOpacity={0.1} label={{ value: '信号估算', position: 'insideTopRight', fill: '#b45309', fontSize: 11 }} />
                )}
                {pricingStaleRange && (
                  <ReferenceArea x1={pricingStaleRange.start} x2={pricingStaleRange.end} fill="#94a3b8" fillOpacity={0.12} label={{ value: '待更新', position: 'insideTopRight', fill: '#64748b', fontSize: 11 }} />
                )}
                <RechartsTooltip formatter={(value: number | string, name: string) => [typeof value === 'number' ? `${value}` : '-', name]} />
                <Legend />
                {pricingSeries.map(series => (
                  <Line
                    key={series.key}
                    type="monotone"
                    dataKey={series.key}
                    name={series.name}
                    stroke={series.color}
                    strokeWidth={series.core ? 2 : 1.8}
                    dot={{ r: series.core ? 3 : 2 }}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <div className="ai-capacity-trend-note">
              <Tag color="magenta">02/23=100</Tag>
              <Tag color="blue">SSD/NAND +{ssdPriceDelta}</Tag>
              <span>{trendHorizon === '1y' ? '2/23 前缺少周频报价观测，留空不补平线；' : ''}公开源多数给涨价区间和报价方向；这里做成报价压力指数，方便和交期同屏比较。</span>
            </div>
          </Card>
        </div>

        {latestIndustryUpdates.length ? (
          <div className="ai-capacity-live-updates">
            <strong>最新公开产业信号</strong>
            {latestIndustryUpdates.map(update => (
              <a href={update.url} target="_blank" rel="noreferrer" key={`${update.date}-${update.title}`}>
                <Tag color={update.type === 'news' ? 'blue' : 'purple'}>{update.date}</Tag>
                <span>{update.title}</span>
              </a>
            ))}
          </div>
        ) : null}

        <div className="ai-capacity-table-wrap">
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            columns={capacityColumns}
            dataSource={capacityMetrics}
          />
        </div>
      </section>

      <div className="ai-supply-chain-kpis">
        <Card size="small">
          <span>最长单点交期</span>
          <strong>120周</strong>
          <small>大型变压器上限</small>
        </Card>
        <Card size="small">
          <span>核心芯片瓶颈</span>
          <strong>6-12个月</strong>
          <small>CoWoS/先进封装</small>
        </Card>
        <Card size="small">
          <span>光互联压力</span>
          <strong>20-52周</strong>
          <small>光纤/互联交付拉长</small>
        </Card>
        <Card size="small">
          <span>可直接入因子</span>
          <strong>{directFactorCount}</strong>
          <small>其余需降权或复核</small>
        </Card>
      </div>

      <div className="ai-supply-chain-toolbar">
        <Segmented
          value={layerFilter}
          onChange={value => setLayerFilter(value as SupplyLayer | 'all')}
          options={[
            { label: '全部', value: 'all' },
            { label: '上游', value: 'upstream' },
            { label: '芯片封装', value: 'chip' },
            { label: '服务器网络', value: 'server' },
            { label: '数据中心', value: 'infra' }
          ]}
        />
        <Tag color={layerFilter === 'all' ? 'blue' : layerMeta[layerFilter].color}>
          当前显示 {filteredNodes.length} 个环节
        </Tag>
        <Button icon={<ThunderboltOutlined />} onClick={() => setSelectedId(criticalPath[0].id)}>
          定位最大瓶颈
        </Button>
      </div>

      <div className="ai-supply-chain-layout">
        <div className="ai-supply-chain-main">
          <div className={`ai-chain-map ${layerFilter === 'all' ? '' : 'filtered'}`}>
            {visibleLayerOrder.map((layer, layerIndex) => (
              <section className="ai-chain-column" key={layer}>
                <div className="ai-chain-column-head" style={{ borderColor: layerMeta[layer].color }}>
                  <strong>{layerMeta[layer].label}</strong>
                  <span>{layerMeta[layer].description}</span>
                </div>
                <div className="ai-chain-node-list">
                  {filteredNodes.filter(node => node.layer === layer).map(node => (
                    <button
                      type="button"
                      key={node.id}
                      className={`ai-chain-node ${selectedNode.id === node.id ? 'selected' : ''}`}
                      onClick={() => setSelectedId(node.id)}
                    >
                      <div className="ai-chain-node-top">
                        <strong>{node.title}</strong>
                        <Tag color={trendMeta[node.trend].color}>{trendMeta[node.trend].label}</Tag>
                      </div>
                      <div className="ai-chain-lead">
                        <ClockCircleOutlined />
                        <span>{formatLead(node)}</span>
                      </div>
                      <Progress
                        percent={node.bottleneckScore}
                        showInfo={false}
                        size="small"
                        strokeColor={node.bottleneckScore >= 90 ? '#dc2626' : layerMeta[node.layer].color}
                      />
                      <div className="ai-chain-node-foot">
                        <span>瓶颈分 {node.bottleneckScore}</span>
                        <span>{dataTierMeta[node.dataTier].label}</span>
                      </div>
                    </button>
                  ))}
                </div>
                {layerFilter === 'all' && layerIndex < layerOrder.length - 1 && (
                  <ArrowRightOutlined className="ai-chain-column-arrow" />
                )}
              </section>
            ))}
          </div>

          <div className="ai-chain-panels">
            <Card title="交付周期排行" className="ai-chain-chart-card">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 20, bottom: 4, left: 78 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" unit="周" />
                  <YAxis type="category" dataKey="name" width={88} tick={{ fontSize: 11 }} />
                  <RechartsTooltip formatter={(value: number, name: string) => [`${value}周`, name === 'max' ? '上限' : '中位']} />
                  <Bar dataKey="max" name="max" fill="#167c80" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card title="关键路径" className="ai-chain-critical-card">
              <div className="ai-chain-critical-list">
                {criticalPath.map((node, index) => (
                  <button type="button" key={node.id} onClick={() => setSelectedId(node.id)}>
                    <span>#{index + 1}</span>
                    <strong>{node.title}</strong>
                    <em>{formatLead(node)}</em>
                  </button>
                ))}
              </div>
            </Card>
          </div>

          <Card className="ai-chain-table-card" title="可量化因子表">
            <Table
              size="small"
              rowKey="id"
              pagination={false}
              columns={tableColumns}
              dataSource={filteredNodes}
            />
          </Card>

          <Card className="ai-chain-data-tier-card" title="数据可信度分层">
            <div className="ai-chain-data-tier-grid">
              {dataQualityRows.map(row => (
                <div key={row.tier}>
                  <Tag color={dataTierMeta[row.tier].color}>{dataTierMeta[row.tier].label}</Tag>
                  <strong>{row.examples}</strong>
                  <span>{row.usage}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <aside className="ai-supply-chain-detail">
          <Card
            title={selectedNode.title}
            extra={<Tag color={layerMeta[selectedNode.layer].color}>{layerMeta[selectedNode.layer].label}</Tag>}
          >
            <div className="ai-chain-detail-metric">
              <span>交付周期</span>
              <strong>{formatLead(selectedNode)}</strong>
            </div>
            <div className="ai-chain-detail-metric">
              <span>瓶颈分</span>
              <Progress percent={selectedNode.bottleneckScore} strokeColor={selectedNode.bottleneckScore >= 90 ? '#dc2626' : '#167c80'} />
            </div>
            <div className="ai-chain-detail-tags">
              <Tooltip title={trendMeta[selectedNode.trend].text}>
                <Tag color={trendMeta[selectedNode.trend].color}>{trendMeta[selectedNode.trend].label}</Tag>
              </Tooltip>
              <Tag color={confidenceMeta[selectedNode.confidence].color}>置信度 {confidenceMeta[selectedNode.confidence].label}</Tag>
              <Tooltip title={dataTierMeta[selectedNode.dataTier].description}>
                <Tag color={dataTierMeta[selectedNode.dataTier].color}>{dataTierMeta[selectedNode.dataTier].label}</Tag>
              </Tooltip>
              <Tooltip title={readinessMeta[selectedNode.factorReadiness].description}>
                <Tag color={readinessMeta[selectedNode.factorReadiness].color}>{readinessMeta[selectedNode.factorReadiness].label}</Tag>
              </Tooltip>
            </div>
            <Paragraph>{selectedNode.factorNote}</Paragraph>

            <div className="ai-chain-detail-metric compact">
              <span>验证/更新</span>
              <strong>{selectedNode.verifiedAt}</strong>
              <small>{selectedNode.updateCadence}</small>
            </div>

            <div className="ai-chain-relation-block">
              <h4><LinkOutlined /> 上游依赖</h4>
              {selectedUpstream.length > 0 ? selectedUpstream.map(edge => {
                const sourceNode = supplyNodes.find(node => node.id === edge.from);
                return (
                  <button type="button" key={`${edge.from}-${edge.to}`} onClick={() => setSelectedId(edge.from)}>
                    <span>{sourceNode?.title}</span>
                    <em>关联强度 {edge.criticality}</em>
                    <small>{edge.label}</small>
                  </button>
                );
              }) : <Text type="secondary">无前置节点，作为上游起点观察。</Text>}
            </div>

            <div className="ai-chain-relation-block">
              <h4><ArrowRightOutlined /> 下游传导</h4>
              {selectedDownstream.length > 0 ? selectedDownstream.map(edge => {
                const targetNode = supplyNodes.find(node => node.id === edge.to);
                return (
                  <button type="button" key={`${edge.from}-${edge.to}`} onClick={() => setSelectedId(edge.to)}>
                    <span>{targetNode?.title}</span>
                    <em>关联强度 {edge.criticality}</em>
                    <small>{edge.label}</small>
                  </button>
                );
              }) : <Text type="secondary">终端瓶颈节点，主要影响上线节奏和资本开支确认。</Text>}
            </div>

            <div className="ai-chain-source-card">
              <h4><DatabaseOutlined /> 数据来源</h4>
              <a href={selectedNode.sourceUrl} target="_blank" rel="noreferrer">{selectedNode.source}</a>
              <p>{selectedNode.sourceNote}</p>
            </div>

            <div className="ai-chain-watch-symbols">
              <h4><SafetyCertificateOutlined /> 相关标的</h4>
              <Space size={[6, 6]} wrap>
                {selectedNode.watchSymbols.map(symbol => <Tag key={symbol}>{symbol}</Tag>)}
              </Space>
            </div>

            <div className="ai-chain-watch-symbols">
              <h4><ThunderboltOutlined /> 自选池命中</h4>
              <Space size={[6, 6]} wrap>
                {matchedWatchlist.length > 0
                  ? matchedWatchlist.slice(0, 8).map(stock => <Tag key={stock.symbol}>{stock.name}</Tag>)
                  : <Text type="secondary">当前自选池暂未命中产业链标的</Text>}
              </Space>
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
};

export default AiSupplyChainCycleCenter;
