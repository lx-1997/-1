import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import MarketPulse from '../../components/MarketPulse';
const metric = { days: 100, return_pct: -2, max_drawdown_pct: -8, sharpe: 0, curve: [] };
const gauge = { available: true, temperature: 32, as_of: '2026-10-27', freshness: 'lagging', signal_usable: false,
  regime_label: '数据待更新', tech_state: '下跌右侧', heat_state: '中性', universe: ['沪深300','中证500'],
  dimensions: [{ key:'trend',name:'趋势结构',score:32,weight:30,used:true }],
  sources:[{name:'沪深300',provider:'sina',as_of:'2026-10-27',cross_checked:false}],
  backtest: {available:true,split_date:'2026-01-01',end:'2026-10-27',validation:'未通过收益对照',
    holdout:{excess_pct:-3,strategy:metric,benchmark:{...metric,return_pct:1},fixed_half:metric},
    folds:[],cost_sensitivity:[{cost_bps_per_side:10,return_pct:-3,excess_pct:-4}],limitations:['指数价格代理']} };
const original = global.fetch;
afterEach(()=>{global.fetch=original;});
test('discloses date and lag, exposes unfavorable benchmark without a precise current allocation',async()=>{
 global.fetch=jest.fn().mockImplementation((url)=>Promise.resolve({ok:true,json:()=>Promise.resolve(String(url).includes('/gauge')?gauge:{})}));
 render(<MarketPulse />);
 await screen.findByText('数据待更新');
 expect(screen.getByText(/2026-10-27.*数据滞后/)).toBeInTheDocument();
 expect(screen.queryByText('仓位指引')).not.toBeInTheDocument();
 expect(screen.getByText('未通过收益对照')).toBeInTheDocument();
 expect(screen.getByText(/-3.00 个百分点/)).toBeInTheDocument();
 expect(screen.getByText(/单源可用/)).toBeInTheDocument();
});
test('failed data does not synthesize neutral scores',async()=>{
 global.fetch=jest.fn().mockResolvedValue({ok:true,json:()=>Promise.resolve({available:false,reason:'四指数共同历史不足'})});
 render(<MarketPulse />);await screen.findByText('四指数共同历史不足');
 expect(screen.queryByText('32')).not.toBeInTheDocument();
});
test('network failure explicitly marks status unavailable',async()=>{
 global.fetch=jest.fn().mockRejectedValue(new Error('offline'));
 render(<MarketPulse />);await waitFor(()=>expect(screen.getByText('状态接口暂不可用')).toBeInTheDocument());
});
