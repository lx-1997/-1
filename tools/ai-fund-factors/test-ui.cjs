/* Run: node tools/ai-fund-factors/test-ui.cjs. No network, trading, or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.resolve(__dirname, '../../deploy/ai-fund.html'), 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n');
new vm.Script(source, { filename: 'ai-fund.html' });
const dom = new JSDOM(html, { url: 'https://example.test/ai-fund', runScripts: 'outside-only' });
const context = dom.getInternalVMContext();
const requests = [];
context.AbortController = AbortController;
context.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
context.requestAnimationFrame = () => 0;
vm.runInContext(source.replace('loadArena();startPoll();', ''), context);
const timers = new Map(); let timerId = 0;
context.setTimeout = (callback, delay) => { assert.equal(delay, 8000); timers.set(++timerId, callback); return timerId; };
context.clearTimeout = id => timers.delete(id);
const run = code => vm.runInContext(code, context);
const response = (symbol, score = 77) => ({ symbol, as_of: '2026-10-19T05:18:00Z',
  capital: { status: 'available', score, label: '资金统计', summary: `${symbol} 资金摘要`, data_date: '2026-10-16', source: '公开资金统计', metrics: [{ label: '主力净流入', value: 0, unit: '亿元' }] },
  chips: { status: 'insufficient', score: null, label: '样本不足', summary: '缺少有效日线', source: '日线', method: '量价估算' },
});
const reply = (request, data) => request.resolve({ ok: true, json: async () => data });
const tick = () => new Promise(resolve => setImmediate(resolve));
const bodyText = () => dom.window.document.getElementById('stockFactors').textContent;
const select = symbol => run(`document.getElementById('stockFactors').dataset.symbol='${symbol}';syncStockFactors()`);

(async () => {
  const historical = run(`evFull({kind:'trade',symbol:'600519',name:'贵州茅台',ts:'2026-10-19T01:50:07Z',scores:{趋势:-.56,成长质量:.67,资金面:null,技术面:'NaN'},confidence:'NaN',composite:'Infinity'})`);
  assert.match(historical, /成交时评分/);
  assert.match(historical, /趋势/);
  assert.match(historical, /成长质量/);
  assert.match(historical, /资金面：当时未记录/);
  assert.match(historical, /筹码结构：当时未记录/);
  assert.match(historical, /查看当前资金与筹码/);
  assert.doesNotMatch(historical, /NaN|Infinity|决策加权综合/);
  assert.match(run(`scoreBars({估值:.2,质量:.3,成长:.4,超跌度:.5,回撤空间:.6,企稳:.7})`), /回撤空间/);
  const missing = run(`stockFactorCard('chips',{status:'insufficient',score:null,metrics:[{label:'无数据',value:NaN,unit:'%'},{label:'异常',value:'Infinity'}],summary:'<script>bad</script>'})`);
  assert.match(missing, /模型估算/);
  assert.match(missing, /&lt;script&gt;/);
  assert.doesNotMatch(missing, /NaN|Infinity|\/ 100|width:50|<script>/);
  assert.match(run(`stockFactorCard('capital',{status:'available',score:0,metrics:[{label:'零值',value:0,unit:'亿元'}]})`), />0<small>\/ 100/);
  assert.match(run(`stockFactorCard('capital',{status:'available',score:0,metrics:[{label:'零值',value:0,unit:'亿元'}]})`), /0亿元/);
  assert.doesNotMatch(run(`stockFactorCard('capital',{status:'available',score:101})`), /\/ 100/);
  assert.doesNotMatch(run(`stockFactorCard('capital',{status:'unavailable',score:50})`), /\/ 100/);
  assert.match(run(`stockFactorCard('capital',{status:'ok',score:26.8})`), />26\.8<small>\/ 100/);
  assert.match(run(`stockFactorMetric({label:'成本区间',value:[146.8233,216.7964],unit:'元'})`), /146\.8233 – 216\.7964元/);
  assert.match(run(`stockFactorMetric({label:'主力净额',value:-100602811,unit:'元'})`), /-1\.006亿元/);
  assert.doesNotMatch(run(`stockFactorMetric({label:'异常区间',value:[null,NaN],unit:'元'})`), /NaN|元/);

  run(`S.mode='detail';S.fund='main';document.getElementById('colCenter').innerHTML='<div id="kchart">chart sentinel</div><section id="stockFactors" data-symbol="600519"></section>';`);
  const a = run('syncStockFactors()');
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /stock-factors\?symbol=600519/);
  const b = select('300760');
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests.length, 2);
  reply(requests[1], response('300760', 88)); await b;
  reply(requests[0], response('600519', 11)); await a;
  assert.match(bodyText(), /300760 资金摘要/);
  assert.doesNotMatch(bodyText(), /600519 资金摘要/);
  assert.equal(dom.window.document.getElementById('kchart').textContent, 'chart sentinel');
  await run('syncStockFactors()');
  assert.equal(requests.length, 2, 'snapshot polling reuses a fresh selected stock');
  run('StockFactors.cache.get("300760").ts-=61000');
  const expired = run('syncStockFactors()');
  assert.equal(requests.length, 3, 'snapshot polling refreshes a selected stock after 60 seconds');
  await run('syncStockFactors()');
  assert.equal(requests.length, 3, 'polling during an in-flight refresh reuses that request');
  reply(requests.at(-1), response('300760', 65)); await expired;

  const c = select('600000');
  reply(requests.at(-1), response('600000', 31)); await c;
  run('StockFactors.cache.get("300760").ts-=61000');
  const d = select('300760');
  assert.equal(requests.length, 5, 'returning to a stock refreshes an expired cache');
  reply(requests.at(-1), response('300760', 64)); await d;
  const cachedCount = requests.length;
  await select('600000'); await select('300760');
  assert.equal(requests.length, cachedCount, 'symbol cache should be reused for 60 seconds');

  const refreshing = run('syncStockFactors(true)');
  const refreshRequest = requests.at(-1);
  run(`S.fund='value';cancelStockFactors();`);
  const switched = run('syncStockFactors(true)');
  assert.equal(refreshRequest.options.signal.aborted, true);
  reply(requests.at(-1), response('300760', 92)); await switched;
  reply(refreshRequest, response('300760', 1)); await refreshing;
  assert.match(bodyText(), /92/);
  assert.doesNotMatch(bodyText(), />1<\/div>/);

  const timeoutPromise = select('600036');
  const timeoutRequest = requests.at(-1), timeoutCallback = [...timers.values()].at(-1);
  timeoutCallback();
  assert.equal(timeoutRequest.options.signal.aborted, true);
  assert.match(bodyText(), /查询超时，请刷新/);
  assert.equal(run('StockFactors.loading'), false);
  const afterTimeout = select('000001');
  reply(requests.at(-1), response('000001', 83)); await afterTimeout;
  reply(timeoutRequest, response('600036', 4)); await timeoutPromise;
  assert.match(bodyText(), /000001 资金摘要/);
  assert.doesNotMatch(bodyText(), /查询超时|600036/);
  const cancelled = select('600009');
  const cancelledRequest = requests.at(-1), cancelledTimeout = [...timers.values()].at(-1);
  const current = select('600010');
  cancelledTimeout();
  assert.doesNotMatch(bodyText(), /查询超时/, 'an old timeout must not overwrite a newer stock');
  reply(requests.at(-1), response('600010', 59)); await current;
  reply(cancelledRequest, response('600009', 9)); await cancelled;
  assert.match(bodyText(), /600010 资金摘要/);
  assert.equal(timers.size, 0, 'all settled requests clear their timeout');

  const mismatched = select('601127');
  reply(requests.at(-1), response('300760')); await mismatched;
  assert.match(bodyText(), /标的校验失败/);
  assert.equal(run(`StockFactors.cache.has('601127')`), false);

  run(`document.getElementById('colRight').innerHTML='<button id="history-trigger">查看当前资金与筹码</button>';openStockFactorModal('002371','北方华创',document.getElementById('history-trigger'));`);
  const modalRequest = requests.at(-1);
  assert.equal(dom.window.document.activeElement.getAttribute('data-factor-close'), '');
  reply(modalRequest, response('002371', 23)); await tick();
  assert.match(dom.window.document.getElementById('factorModalBody').textContent, /002371 资金摘要/);
  const lastSummary = [...dom.window.document.querySelectorAll('#factorModal summary')].at(-1);
  lastSummary.focus();
  dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
  assert.equal(dom.window.document.activeElement.getAttribute('data-factor-close'), '');
  dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(dom.window.document.getElementById('factorModal').style.display, 'none');
  assert.equal(dom.window.document.activeElement.id, 'history-trigger');
  if (requests.at(-1) !== modalRequest) reply(requests.at(-1), response('601127'));
  await tick();

  const leaving = run('syncStockFactors(true)');
  const pending = requests.at(-1);
  run(`setMode('arena')`);
  assert.equal(pending.options.signal.aborted, true);
  reply(pending, response('601127')); await leaving;
  assert.equal(run('StockFactors.key'), '');
  assert.equal(run('StockFactors.modal'), null);
  assert.equal(timers.size, 0);
  console.log('PASS: syntax, historical/current distinction, all score dimensions, missing/NaN/zero values, escaping, request races, 60s cache and automatic refresh, 8s timeout and cancellation isolation, strategy cancellation, modal and focus, arena cancellation');
  dom.window.close();
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
