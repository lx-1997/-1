"""Config UI composed onto the existing authenticated research workspace."""
import json
from .quant_research_config import DEFAULT

FIELDS = [
 ('rebalance_days','调仓间隔（交易日）',1,20,1,1),
 ('rebalance_band','最小调仓差额（净值 %）',0,10,.1,100),
 ('max_holdings','最多持股数',1,5,1,1),
 ('single_weight','单股仓位上限（%）',1,20,1,100),
 ('gross_weight','组合仓位上限（%）',1,80,1,100),
 ('stop_loss','个股止损（%）',1,8,.5,100),
 ('drawdown_pause','永久风险暂停阈值（%）',1,8,.5,100),
 ('slippage_bps','单边滑点（基点 bps）',10,100,1,1),
 ('volume_participation','昨日成交量参与率上限（%）',.01,1,.01,100),
 ('commission_rate','佣金费率（%）',.025,1,.005,100),
 ('minimum_commission','最低单笔佣金（元）',5,100,1,1),
 ('sell_stamp_rate','卖出印花税假设（%）',.05,1,.01,100),
 ('transfer_rate','过户费假设（%）',.001,.1,.001,100),
 ('min_price','昨日收盘最低价格（元）',0,10000,.01,1),
 ('min_turnover','昨日最低成交额代理（万元）',0,1000000,100,1/10000),
 ('market_sma_days','基准趋势均线（交易日）',20,120,1,1),
 ('volatility_days','波动率观察窗口（交易日）',10,60,1,1),
 ('vol_target','组合年化波动目标上限（%）',1,8,.5,100),
]

def enhance(html):
    def once(old,new):
        nonlocal html
        if html.count(old)!=1: raise ValueError('Config UI integration anchor changed: '+old[:70])
        html=html.replace(old,new,1)
    basic = '<div class="config-basic"><label class="field">策略模板<select id="cfg_strategy"><option value="momentum">价格动量</option><option value="low_turnover">低换手动量</option><option value="trend_guard">基准趋势过滤</option><option value="risk_scaled">趋势过滤 + 波动降仓</option></select></label><label class="field">模拟本金（元）<input id="cfg_capital" type="number" min="10000" max="100000000" step="10000" value="1000000" required></label><p class="muted config-hint">收益率为主；本金影响整手和最低佣金，实际参与撮合。</p><label class="field">动量周期候选（交易日）<input id="cfg_lookbacks" value="10, 20, 40" required aria-describedby="gridHint"></label><label class="field">入场动量阈值候选（%）<input id="cfg_thresholds" value="0, 3" required aria-describedby="gridHint"></label><p class="muted config-hint" id="gridHint">逗号分隔。周期 5–120 日、最多 4 个；阈值 0–20%、最多 3 个；总共最多 12 组，仅用训练区选优。</p></div>'
    fields=[]
    for key,label,low,high,step,scale in FIELDS:
        condition=' data-trend="true"' if key=='market_sma_days' else ' data-vol="true"' if key in ('volatility_days','vol_target') else ''
        fields.append(f'<label class="field"{condition}>{label}<input id="cfg_{key}" type="number" min="{low}" max="{high}" step="{step}" value="{DEFAULT[key]*scale:g}" required></label>')
    advanced='<details class="config-advanced"><summary>高级配置 <span class="muted">调仓 · 风险 · 成本</span></summary><div class="config-fields">'+''.join(fields)+'</div><p class="muted config-hint">费用是保守模拟假设，不代表券商报价；滑点至少 10 bps。风险只能收紧。仓位为下单目标，价格波动和卖出受阻会使实际敞口偏离。成交额代理为昨日收盘价 × 股数，未核验 ST / IPO / 退市状态，不能据此声明全市场可交易。</p></details>'
    locked='<div class="config-locked"><b>验证协议 · 锁定</b><p>训练至少 126 日 · 样本外每窗 42 日<br>隔离 5 日 · 最终留出 63 日<br>至少两个样本外窗口；成本后收益为正；留出跑赢基准；回撤不超过 8%。</p><p>修改参数会新建开发实验，需冻结后用新数据验证，不能凭历史调参直接晋级。</p></div>'
    once('<div class="method">',basic+advanced+locked+'<div class="method">')
    once('固定候选：10 / 20 / 40 日动量，0% / 3% 信号阈值。训练选参后冻结，最终 63 日单独留出。','候选和配置在提交时封存。信号仅使用已完成的历史日线，下一交易日开盘模拟执行。')
    once('</style>',CSS+'</style>')
    once("end_date:$('end').value};", "end_date:$('end').value,capital:configCapital(),configuration:configRead()};")
    if 'function render(job){renderCore(job);' in html:
        once('function render(job){renderCore(job);','function render(job){renderCore(job);renderConfigSnapshot(job);')
    else:
        once('function render(job){','function render(job){renderConfigBase(job);renderConfigSnapshot(job)}\nfunction renderConfigBase(job){')
    once('昨日收盘信号，下一交易日开盘模拟执行；滑点 10bps；采用上一日成交量 1% 的参与率上限。单股上限 20%，组合上限 80%，回撤 8% 暂停新增风险。费用为明确的模拟假设。','昨日收盘信号，下一交易日开盘模拟执行。实际仓位、成本、过滤和调仓规则见本次封存配置；无法成交时不会假定已卖出或绕过交易限制。')
    once('</script>', 'const CONFIG_DEFAULT='+json.dumps(DEFAULT,ensure_ascii=False)+';\nconst CONFIG_FIELDS='+json.dumps(FIELDS,ensure_ascii=False)+';\n'+SCRIPT+'\n</script>')
    return html

CSS = r"""
.config-hint{font-size:11px;line-height:1.7;margin-top:8px}.config-advanced{margin-top:20px;border-top:1px solid var(--line);padding-top:16px}.config-advanced summary{font-weight:600;font-size:13px;gap:8px}.config-advanced summary span{font-size:10px;font-weight:400}.config-locked{margin-top:18px;border-radius:10px;padding:12px;background:var(--soft);font-size:11px;line-height:1.8}.config-locked p{margin-top:6px;color:var(--muted)}.config-snapshot{margin-top:18px;overflow-wrap:anywhere}.config-snapshot dl{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}.config-snapshot dt{font-size:11px;color:var(--muted)}.config-snapshot dd{margin:3px 0 0;font-size:13px}.config-snapshot code{font-size:10px;word-break:break-all}.config-fields{display:grid;gap:0 12px;grid-template-columns:1fr 1fr}.config-fields .field{min-width:0;font-size:11px}.config-fields input{padding:9px;font-size:12px}.config-comparison{margin-top:18px}.config-comparison h3{font-size:14px;margin-bottom:8px}.config-snapshot .tag{white-space:normal}.sidebar input,.sidebar select{min-width:0;max-width:100%;width:100%}header nav{flex-wrap:wrap}header{height:auto;min-height:68px;gap:10px;padding-top:12px;padding-bottom:12px}header nav a{white-space:nowrap}@media(min-width:1001px){.layout{grid-template-columns:320px minmax(0,1fr)}}@media(max-width:700px){header nav{font-size:10px;gap:8px}header{align-items:flex-start}.brand{flex-wrap:wrap}.config-snapshot dl{grid-template-columns:repeat(2,minmax(0,1fr))}.config-fields{grid-template-columns:1fr 1fr}.config-advanced summary{min-height:36px}.config-locked{font-size:12px}input,select{font-size:16px}.config-fields input{font-size:16px}.config-snapshot .row button{width:100%}}
"""
SCRIPT = r'''
function configCapital(){const n=Number($('cfg_capital').value);if(!Number.isFinite(n)||n<10000||n>100000000)throw Error('模拟本金须为一万至一亿元');return n}
function configRead(){
 const list=(id,scale)=>{const text=$(id).value.trim();if(!text||!/^[0-9.,，\s]+$/.test(text))throw Error('候选请填写逗号分隔的数字');const parts=text.split(/[,，\s]+/).filter(Boolean);return parts.map(v=>Number(v)/scale)};
 const c={...CONFIG_DEFAULT,strategy:$('cfg_strategy').value,lookbacks:list('cfg_lookbacks',1),thresholds:list('cfg_thresholds',100)};
 for(const [key,label,low,high,step,scale] of CONFIG_FIELDS){const text=$('cfg_'+key).value;if(!text.trim())throw Error('请填写'+label);const n=Number(text);if(!Number.isFinite(n)||n<low||n>high)throw Error(label+'超出允许范围');c[key]=n/scale}
 if(c.lookbacks.length>4||c.thresholds.length>3||c.lookbacks.length*c.thresholds.length>12||c.lookbacks.some(v=>!Number.isInteger(v)||v<5||v>120)||c.thresholds.some(v=>v<0||v>.2)||new Set(c.lookbacks).size!==c.lookbacks.length||new Set(c.thresholds).size!==c.thresholds.length)throw Error('候选周期、阈值或数量不合法，单次最多十二组');
 return c;
}
function configVisibility(){const strategy=$('cfg_strategy').value;document.querySelectorAll('[data-trend]').forEach(n=>n.classList.toggle('hidden',!['trend_guard','risk_scaled'].includes(strategy)));document.querySelectorAll('[data-vol]').forEach(n=>n.classList.toggle('hidden',strategy!=='risk_scaled'))}
function renderConfigSnapshot(job){
 if(job.status!=='completed'||job.request.kind)return;
 const c=job.result.protocol?.configuration||job.request.configuration||CONFIG_DEFAULT;
 const names={momentum:'价格动量',low_turnover:'低换手动量',trend_guard:'基准趋势过滤',risk_scaled:'趋势过滤 + 波动降仓'};
 const rows=[['策略模板',names[c.strategy]||c.strategy],['候选周期',c.lookbacks.join(' / ')+' 日'],['候选阈值',c.thresholds.map(v=>(v*100).toFixed(2)+'%').join(' / ')],...CONFIG_FIELDS.filter(([k])=>!(['market_sma_days','volatility_days','vol_target'].includes(k))||k==='market_sma_days'&&['trend_guard','risk_scaled'].includes(c.strategy)||c.strategy==='risk_scaled').map(([k,label,low,high,step,scale])=>[label,Number((c[k]*scale).toFixed(6))])];
 $('result').insertAdjacentHTML('beforeend',`<section class="panel config-snapshot"><div class="sectionhead"><div><div class="eyebrow">FROZEN CONFIGURATION</div><h2>本次实际执行配置</h2></div><span class="tag">${job.result.protocol?.configuration?'自定义开发实验':'原基线协议'}</span></div><dl>${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl><p class="muted">验证协议：126 日训练起步 / 42 日样本外窗口 / 5 日隔离 / 63 日最终留出。费用、数据指纹和失败结果均保留。</p>${job.result.protocol?.configuration_digest?`<p class="muted">配置指纹 <code>${esc(job.result.protocol.configuration_digest)}</code></p>`:''}<div class="row" style="margin-top:14px"><button type="button" data-reuse-config="${esc(job.id)}">复制配置，新建实验</button><button type="button" data-compare-config="true">对比最近三个实验</button></div><div id="configComparison"></div></section>`)
}
$('cfg_strategy').addEventListener('change',()=>{const type=$('cfg_strategy').value;$('cfg_rebalance_days').value=type==='momentum'?'1':'5';$('cfg_rebalance_band').value=type==='momentum'?'0':'2';configVisibility()});
document.addEventListener('click',event=>{const b=event.target.closest('[data-reuse-config]');if(!b||selected?.id!==b.dataset.reuseConfig)return;const c=selected.result.protocol?.configuration||selected.request.configuration||CONFIG_DEFAULT;for(const [key,label,low,high,step,scale] of CONFIG_FIELDS)$('cfg_'+key).value=String(c[key]*scale);$('cfg_strategy').value=c.strategy;$('cfg_lookbacks').value=c.lookbacks.join(', ');$('cfg_thresholds').value=c.thresholds.map(v=>Number((v*100).toFixed(6))).join(', ');$('cfg_capital').value=String(selected.request.capital||1000000);$('name').value=(selected.request.name+' · 对照').slice(0,80);$('symbols').value=selected.request.symbols.join(', ');$('benchmark').value=selected.request.benchmark;$('start').value=selected.request.start_date;$('end').value=selected.request.end_date;$('researchForm').classList.add('expanded');$('toggleForm').setAttribute('aria-expanded','true');$('toggleForm').textContent='收起设置';configVisibility();$('researchForm').scrollIntoView({block:'start'});notify('已复制参数，提交后会生成新的实验；原记录保留')});
document.addEventListener('click',async event=>{const b=event.target.closest('[data-compare-config]');if(!b||b.disabled)return;const epoch=generation,id=selected?.id,t=token();b.disabled=true;try{const listed=await api('/experiments');if(epoch!==generation||t!==token()||id!==selected?.id)return;const items=listed.items.filter(x=>x.status==='completed'&&!x.request.kind).slice(0,3);const records=await Promise.all(items.map(x=>api('/experiments/'+x.id)));if(epoch!==generation||t!==token()||id!==selected?.id)return;$('configComparison').innerHTML=`<div class="config-comparison"><h3>实验对照 · 不自动排名</h3><p class="muted">历史区间或基准不同的结果不能直接比较；所有失败和开发状态继续保留。</p><div class="tablewrap"><table><thead><tr><th>实验</th><th>区间 / 基准</th><th>策略 / 调仓</th><th>样本外收益</th><th>留出超额</th><th>门槛</th></tr></thead><tbody>${records.map(r=>{const c=r.result.protocol?.configuration||r.request.configuration||CONFIG_DEFAULT;return `<tr><td>${esc(r.request.name)}<br>${esc(r.id.slice(0,8))}</td><td>${esc(r.request.start_date)} → ${esc(r.request.end_date)}<br>${esc(r.request.benchmark)}</td><td>${esc(c.strategy)} / ${esc(c.rebalance_days)}日</td><td>${pct(r.result.oos.return_pct)}</td><td>${pct(r.result.holdout.metrics.excess_pct)}</td><td>${r.result.gate.passed?'通过本轮门槛':'未通过 / 开发研究'}</td></tr>`}).join('')}</tbody></table></div></div>`}catch(e){showError(e.message)}finally{if(b.isConnected)b.disabled=false}});
configVisibility();
'''