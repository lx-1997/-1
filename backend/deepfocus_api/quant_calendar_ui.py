"""Calendar-quality UI composition, preserving a single CSP-hashed script."""
SCRIPT = r'''
let calendarItems=[],calendarError='',calendarSubmitting=false;
function renderCalendarForm(job){
 const first=Number(job.request.start_date?.slice(0,4)),last=Number(job.request.end_date?.slice(0,4));
 const available=calendarItems.filter(b=>Array.from({length:Math.max(0,last-first+1)},(_,i)=>first+i).every(y=>b.years.includes(y)));
 $('result').insertAdjacentHTML('beforeend',`<section class="panel" style="margin-top:18px"><div class="eyebrow">DATA QUALITY / CALENDAR</div><h2>独立核对交易日期</h2><p class="muted" style="margin-top:8px">用留存的沪深交易所年度休市通知核对封存行情，可发现所有序列共同漏日。保留原实验，不重新选参或改写收益。</p><label class="field">我的日历证据<select id="calendarBundle" ${available.length?'':'disabled'}>${available.map(b=>`<option value="${esc(b.id)}">${esc(b.label)} · ${esc(b.years.join(' / '))}</option>`).join('')||'<option>暂无覆盖本实验年份的日历证据</option>'}</select></label><p class="muted" style="margin-top:10px">${calendarError?'日历证据服务暂不可用，请刷新档案重试。':available.length?'年度计划核对不等于临时停市、实际会话或个股状态完整认证。':'由内部管理员导入已核对的官方原文；不会自动猜测未覆盖年份。'}</p><button style="margin-top:14px" data-calendar="${esc(job.id)}" ${available.length&&!calendarError?'':'disabled'}>核对年度休市计划</button></section>`)
}
function renderCalendar(job){
 const r=job.result,q=r.quality,ok=q.status==='matches_plan',rows=Object.entries(q.symbols);
 $('result').innerHTML=`<section class="panel"><div class="eyebrow">CALENDAR / QUALITY REVIEW</div><div class="sectionhead"><div><h2>${ok?'年度休市计划核对一致':'发现交易日期差异'}</h2><p class="muted" style="margin-top:8px">${esc(job.request.name)}<br>${job.request.verification_of?'冻结结果已复核一致 · ':''}请求 ${esc(q.requested_start)} → ${esc(q.requested_end)}<br>按原采集时钟核对至 ${esc(q.completed_through)}</p></div><span class="tag">数据核验 · 不构成策略晋级</span></div><div class="metrics">${countMetric('核对序列',rows.length,'股票与价格基准')}${countMetric('官方通知',r.evidence.source_count,'留存原文，不公开下载')}${countMetric('缺失计划日期',rows.reduce((n,[,v])=>n+v.missing_planned_sessions.length,0),'按序列累计，不是独立日期数')}${countMetric('计划外日期',rows.reduce((n,[,v])=>n+v.unexpected_sessions.length,0),'不会静默删除或补价')}</div><div class="note">${job.kernel_current===false?'执行版本已变化，原记录保留，不能冒充当前版本复核。':'本记录只核对年度休市计划，不改变原实验结果。'}<br>临时停市、盘中事件和历史证券状态仍未完整核验；日期一致不证明策略有效，不开启模拟账户。<br>采集当天未到北京时间16:30时，不要求尚未完成的当日日线。</div><div class="row" style="margin-top:16px"><button data-job="${esc(job.request.parent_id)}">查看原实验</button><button data-export="${esc(job.id)}">导出日历核验</button><button data-replay="${esc(job.id)}" ${job.kernel_current===false?'disabled':''}>冻结日历复核</button></div></section><section class="panel" style="margin-top:18px"><h2>每条序列的日期核对</h2><div class="tablewrap"><table><thead><tr><th>代码</th><th>计划日数</th><th>实际日数</th><th>核对结果</th></tr></thead><tbody>${rows.map(([s,v])=>`<tr><td>${esc(s)}</td><td>${Number(v.expected_sessions)}</td><td>${Number(v.observed_sessions)}</td><td>${v.status==='matches_plan'?'与年度计划一致':'存在差异'}</td></tr>`).join('')}</tbody></table></div>${rows.filter(([,v])=>v.missing_planned_sessions.length||v.unexpected_sessions.length).map(([s,v])=>`<details style="margin-top:14px"><summary><span>${esc(s)} · 查看缺失与额外日期</span></summary><div class="muted" style="overflow-wrap:anywhere">缺失：${esc(v.missing_planned_sessions.join('、')||'无')}<br>计划外：${esc(v.unexpected_sessions.join('、')||'无')}</div></details>`).join('')}<p class="muted" style="margin-top:14px">${esc(r.evidence.label)}<br>原行情 ${esc(r.source_dataset_digest.slice(0,20))}…<br>日历证据 ${esc(r.evidence.fingerprint.slice(0,20))}…</p></section>`
}
async function startCalendarReview(button){
 if(calendarSubmitting)return;calendarSubmitting=true;button.disabled=true;const text=button.textContent;button.textContent='正在创建日历核验…';
 try{
  const parent=button.dataset.calendar,input={bundle_id:$('calendarBundle').value},signature=JSON.stringify([parent,input]);
  let pending;try{pending=JSON.parse(sessionStorage.getItem('quant_calendar_pending')||'null')}catch{}
  if(!pending||pending.signature!==signature)pending={signature,id:crypto.randomUUID()};
  sessionStorage.setItem('quant_calendar_pending',JSON.stringify(pending));
  const job=pending.job_id?await api('/experiments/'+pending.job_id):await api('/experiments/'+parent+'/calendar-review',{method:'POST',body:JSON.stringify({...input,request_id:pending.id})});
  pending.job_id=job.id;sessionStorage.setItem('quant_calendar_pending',JSON.stringify(pending));render(job);
  await select(job.id);await load();sessionStorage.removeItem('quant_calendar_pending');
  if(window.innerWidth<=700)document.querySelector('main').scrollIntoView({behavior:'auto',block:'start'});
 }catch(e){if(selected?.status==='running')retryPoll(selected.id,5000);throw e}
 finally{calendarSubmitting=false;if(button.isConnected){button.disabled=false;button.textContent=text}}
}
'''


def enhance(html):
    def once(needle, replacement):
        nonlocal html
        if html.count(needle) != 1:
            raise ValueError('Calendar research UI anchor changed')
        html = html.replace(needle, replacement, 1)
    once("function render(job){renderCore(job);if(job.status==='completed'&&job.request.kind!=='event_replay')renderEvidenceForm(job)}",
         SCRIPT+"\nfunction render(job){renderCore(job);if(job.status==='failed'&&job.request.kind==='event_replay'&&job.quality?.annual_plan)renderEventCalendarFailure(job);if(job.status==='completed'&&!job.request.kind){renderEvidenceForm(job);renderCalendarForm(job)}}")
    once("if(job.request.kind==='event_replay'){renderEvent(job);return}",
         "if(job.request.kind==='calendar_review'){renderCalendar(job);return}if(job.request.kind==='event_replay'){renderEvent(job);return}")
    once("const [data,papers,proof]=await Promise.all([api('/experiments'),api('/paper'),api('/evidence').then(d=>({...d,ok:true})).catch(e=>({items:[],ok:false,message:e.message}))]);evidenceItems=proof.items;evidenceError=proof.ok?'':proof.message;",
         "const [data,papers,proof,calendarProof]=await Promise.all([api('/experiments'),api('/paper'),api('/evidence').then(d=>({...d,ok:true})).catch(e=>({items:[],ok:false,message:e.message})),api('/calendar-evidence').then(d=>({...d,ok:true})).catch(e=>({items:[],ok:false,message:e.message}))]);evidenceItems=proof.items;evidenceError=proof.ok?'':proof.message;calendarItems=calendarProof.items;calendarError=calendarProof.ok?'':calendarProof.message;")
    once("if(b.dataset.event){await startEventReplay(b);return}",
         "if(b.dataset.calendar){await startCalendarReview(b);return}if(b.dataset.event){await startEventReplay(b);return}")
    once("function reset(){", "function reset(){calendarItems=[];calendarError='';evidenceItems=[];evidenceError='';sessionStorage.removeItem('quant_calendar_pending');")
    return html
