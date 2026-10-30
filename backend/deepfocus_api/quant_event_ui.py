"""Event UI composition; exact anchors fail closed if the base layout changes."""
SCRIPT=r'''
let evidenceItems=[],evidenceError='',eventSubmitting=false;
function countMetric(label,value,sub=''){return `<div class="metric"><small>${esc(label)}</small><strong>${Number(value)||0}</strong><small>${esc(sub)}</small></div>`}
function render(job){renderCore(job);if(job.status==='completed'&&job.request.kind!=='event_replay')renderEvidenceForm(job)}
function renderEvidenceForm(job){
 const available=evidenceItems.filter(b=>job.request.symbols.some(s=>Object.hasOwn(b.symbols,s)));
 $('result').insertAdjacentHTML('beforeend',`<section class="panel" style="margin-top:18px"><div class="eyebrow">EVIDENCE / EVENT REPLAY</div><h2>分红与税务事件重放</h2><p class="muted" style="margin-top:8px">保留原实验，按原冻结参数路径重新生成订单。当前仅支持抽样开发核验，不重新评估最终留出集，不开启模拟账户。</p><label class="field">我的证据包<select id="eventBundle" ${available.length?'':'disabled'}>${available.map(b=>`<option value="${esc(b.id)}">${esc(b.label)} · ${b.event_count} 条抽样记录</option>`).join('')||'<option>暂无匹配证据包</option>'}</select></label><label class="field">信息可用模式<select id="eventMode"><option value="as_recorded">按当时实际留存的信息</option><option value="historical_reconstruction">事后重建（后补公告，已见数据）</option></select></label><p class="muted" style="margin-top:10px">${evidenceError?'证据服务暂不可用，请刷新档案后重试。':available.length?'原文指纹已保存；事件字段和覆盖完整性尚未核验。':'证据由内部管理员导入。没有记录不能推断没有公司行为。'}</p><button class="primary" style="margin-top:14px" data-event="${esc(job.id)}" ${available.length&&!evidenceError?'':'disabled'}>按冻结参数重放事件</button></section>`)
}
function renderEvent(job){
 const r=job.result,b=r.state.book,proof=r.evidence,mode=job.request.knowledge_mode==='historical_reconstruction'?'事后重建 · 已见数据':'当时实际留存';
 const covered=job.request.symbols.filter(s=>Object.hasOwn(proof.symbols,s)).length;
 $('result').innerHTML=`<section class="panel"><div class="eyebrow">EVENT REPLAY / DEVELOPMENT</div><div class="sectionhead"><div><h2>${esc(job.request.name)}</h2><p class="muted">${job.request.verification_of?'冻结事件重放核对一致 · ':''}${esc(mode)} · ${esc(job.id.slice(0,8))} · ${r.curve.length} 个交易日<br>原样本外区间 ${esc(r.curve[0]?.date)} → ${esc(r.curve.at(-1)?.date)}；最终留出未重新评估。</p></div><span class="tag">抽样资料 · 禁止晋级</span></div><div class="metrics">${metric('研究区间收益率',r.metrics.return_pct,'已预留分红税的风险净值')}${metric('最大回撤',r.metrics.max_drawdown_pct,'该区间收盘风险净值')}${countMetric('模拟成交',r.fills.length,'重新生成，未写入原账户')}${countMetric('有抽样记录的股票',covered,`研究共 ${job.request.symbols.length} 只；完整覆盖未核验`)}</div>${chart(r.curve)}<div class="note">${job.kernel_current===false?'冻结执行版本已变化，原记录保留，不能用当前版本冒充复核。':'开发重放已完成；这不等于新的样本外验证。'}<br>${r.state.paused?'样本外账户已触发永久风险暂停，未为改善结果而解除。':''}<br>证据为抽样资料，不能据此断言完整总收益或策略增强。不会创建模拟账户或真实订单。</div><div class="row" style="margin-top:16px"><button data-job="${esc(job.request.parent_id)}">查看原实验</button><button data-export="${esc(job.id)}">导出事件研究</button><button data-replay="${esc(job.id)}" ${job.kernel_current===false?'disabled':''}>冻结事件复核</button></div></section><details class="panel" style="margin-top:18px" open><summary><h2>数据质量与证据</h2></summary><div><p class="muted">${esc(proof.label)} · 原文 ${proof.source_count} 份 · 覆盖未核验<br>当前情景为境内个人公开市场 A 股，不代表你的实际账户税务身份。</p><div class="tablewrap"><table><thead><tr><th>股票</th><th>导入记录</th><th>覆盖状态</th></tr></thead><tbody>${job.request.symbols.map(s=>`<tr><td>${esc(s)}</td><td>${Number(proof.symbols[s])||0}</td><td>${proof.symbols[s]?'抽样；完整性未核验':'暂无记录；不能推断没有公司行为'}</td></tr>`).join('')}</tbody></table></div><p class="muted">原行情指纹 ${esc(r.source_dataset_digest.slice(0,20))}…<br>证据指纹 ${esc(proof.fingerprint.slice(0,20))}…<br>内核 ${esc(job.kernel.slice(0,20))}…</p></div></details><details class="panel" style="margin-top:18px"><summary><h2>成交与现金核对</h2></summary><div><p class="muted">分红税前到账 ${Number(b.paid_gross).toFixed(2)} 元；已评定分红税 ${Number(b.tax_assessed).toFixed(2)} 元。资金时点为明确研究模型，仍待清算账单核对。</p><div class="tablewrap"><table><thead><tr><th>日期</th><th>股票</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>${r.fills.slice(-30).reverse().map(f=>`<tr><td>${esc(f.date)}</td><td>${esc(f.symbol)}</td><td>${f.side==='buy'?'买入':'卖出'}</td><td>${f.qty}</td><td>${Number(f.price).toFixed(2)}</td><td>${Number(f.fee).toFixed(2)}</td></tr>`).join('')||'<tr><td colspan="6">本区间无成交，不能据此证明策略有效。</td></tr>'}</tbody></table></div></div></details>`
}
async function startEventReplay(button){
 if(eventSubmitting)return;eventSubmitting=true;button.disabled=true;const text=button.textContent;button.textContent='正在创建事件研究…';
 try{
  const input={bundle_id:$('eventBundle').value,knowledge_mode:$('eventMode').value},parent=button.dataset.event,signature=JSON.stringify([parent,input]);
  let pending;try{pending=JSON.parse(sessionStorage.getItem('quant_event_pending')||'null')}catch{}
  if(!pending||pending.signature!==signature)pending={signature,id:crypto.randomUUID()};
  sessionStorage.setItem('quant_event_pending',JSON.stringify(pending));
  const job=pending.job_id?await api('/experiments/'+pending.job_id):await api('/experiments/'+parent+'/event-replay',{method:'POST',body:JSON.stringify({...input,request_id:pending.id})});
  pending.job_id=job.id;sessionStorage.setItem('quant_event_pending',JSON.stringify(pending));render(job);
  await select(job.id);await load();sessionStorage.removeItem('quant_event_pending');
  if(window.innerWidth<=700)document.querySelector('main').scrollIntoView({behavior:'auto',block:'start'});
 }catch(e){if(selected?.status==='running')retryPoll(selected.id,5000);throw e}
 finally{eventSubmitting=false;if(button.isConnected){button.disabled=false;button.textContent=text}}
}
'''


def enhance(html):
    def once(needle,replacement):
        nonlocal html
        if html.count(needle)!=1:raise ValueError('Research UI integration anchor changed')
        html=html.replace(needle,replacement,1)
    once('function render(job){',SCRIPT+'\nfunction renderCore(job){')
    once("const r=job.result,m=chartMode===","if(job.request.kind==='event_replay'){renderEvent(job);return}\nconst r=job.result,m=chartMode===")
    once("const [data,papers]=await Promise.all([api('/experiments'),api('/paper')]);",
         "const [data,papers,proof]=await Promise.all([api('/experiments'),api('/paper'),api('/evidence').then(d=>({...d,ok:true})).catch(e=>({items:[],ok:false,message:e.message}))]);evidenceItems=proof.items;evidenceError=proof.ok?'':proof.message;")
    once('await renderPapers(papers.items)}',"await renderPapers(papers.items);if(selected?.status==='completed')render(selected)}")
    once("if(b.dataset.replay){b.disabled=true;","if(b.dataset.event){await startEventReplay(b);return}if(b.dataset.replay){b.disabled=true;")
    once("if(pending?.job_id===id)sessionStorage.removeItem('quant_research_pending')",
         "if(pending?.job_id===id)sessionStorage.removeItem('quant_research_pending');const eventPending=JSON.parse(sessionStorage.getItem('quant_event_pending')||'null');if(eventPending?.job_id===id)sessionStorage.removeItem('quant_event_pending')")
    once('研究结果仅供模拟验证。公开数据商业授权、公司行为及特殊交易状态覆盖尚待核验。',
         '当前用于内部研究。公司行为、交易状态和含分红基准未完整核验，不将开发结果作为盈利承诺。')
    return html
