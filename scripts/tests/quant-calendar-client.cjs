// Runs the shipped client in a VM with fixture transport; no browser credentials.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_research_ui import HTML; print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const script=html.split('<script>')[1].split('</script>')[0],nodes=new Map(),listeners=new Map();
function node(id){if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,value:'',innerHTML:'',textContent:'',disabled:false,isConnected:true,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},setAttribute(){},insertAdjacentHTML(where,s){this.innerHTML+=s},scrollIntoView(){this.scrolled=true},addEventListener(t,f){listeners.set(id+':'+t,f)}})}return nodes.get(id)}
function storage(){const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,String(v)),removeItem:k=>m.delete(k)}}
const auth=storage(),session=storage();auth.setItem('auth_token','fixture-only');let posts=0,firstRead=true;
const request={kind:'calendar_review',name:'fixture',parent_id:'parent',symbols:['000333'],knowledge_mode:'as_recorded'};
const job={id:'accepted-event',status:'running',request,stage:'fixture running',progress:20,events:[]};
const response=(status,data,type='application/json')=>({ok:status>=200&&status<300,status,headers:{get:()=>type},json:async()=>data});
async function fetch(url,options={}){
 if(url.endsWith('/calendar-review')){posts++;assert.equal(JSON.parse(options.body).bundle_id,'bundle');return response(202,job)}
 if(url.endsWith('/experiments/accepted-event')){if(firstRead){firstRead=false;return response(502,null,'text/html')}return response(200,{...job,status:'failed',stage:'fixture terminal'})}
 if(url.endsWith('/experiments')||url.endsWith('/paper'))return response(200,{items:[]});
 if(url.endsWith('/evidence')||url.endsWith('/calendar-evidence'))return response(200,{items:[]});
 throw Error('unexpected fixture request '+url);
}
const document={body:node('body'),hidden:false,getElementById:node,querySelector:()=>node('main'),addEventListener(t,f){listeners.set('document:'+t,f)}};
const context={document,localStorage:auth,sessionStorage:session,fetch,AbortController,Date,JSON,Number,String,Object,Set,Promise,console,crypto:{randomUUID:()=> 'frozen-request-key'},setTimeout:()=>1,clearTimeout(){},window:{innerWidth:390,addEventListener(){},matchMedia:()=>({matches:false})}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 await new Promise(setImmediate);node('calendarBundle').value='bundle';
 const button={dataset:{calendar:'parent'},disabled:false,textContent:'核对',isConnected:true};
 await assert.rejects(context.startCalendarReview(button),/服务暂时繁忙/);
 assert.equal(posts,1);assert.equal(JSON.parse(session.getItem('quant_calendar_pending')).job_id,'accepted-event');assert.equal(button.disabled,false);
 await context.startCalendarReview(button);assert.equal(posts,1,'retry must GET accepted task');assert.equal(session.getItem('quant_calendar_pending'),null);
 vm.runInContext(`calendarItems=[{id:'bundle',label:'<img src=x onerror=alert(1)>',years:[2023,2024,2025,2026]}];renderCalendarForm({id:'parent',request:{start_date:'2023-10-26',end_date:'2026-10-30'}})`,context);
 assert.match(node('result').innerHTML,/&lt;img/);assert.doesNotMatch(node('result').innerHTML,/<img src=x/);
 const result={quality:{status:'rejected',requested_start:'2023-10-26',requested_end:'2026-10-30',completed_through:'2026-10-29',symbols:{'600519':{expected_sessions:729,observed_sessions:728,missing_planned_sessions:['2025-04-28'],unexpected_sessions:[],status:'rejected'}}},evidence:{source_count:8,label:'annual fixture',fingerprint:'evidence'},source_dataset_digest:'source'};
 context.render({...job,status:'completed',kernel:'fixture',kernel_current:true,result});
 assert.match(node('result').innerHTML,/发现交易日期差异/);assert.match(node('result').innerHTML,/2025-04-28/);assert.match(node('result').innerHTML,/临时停市/);
 assert.doesNotMatch(node('result').innerHTML,/data-promote/);assert.doesNotMatch(node('result').innerHTML,/data-chart/);assert.doesNotMatch(node('result').innerHTML,/成本后收益率/);
 context.render({...job,request:{...request,verification_of:'original'},status:'completed',kernel:'fixture',kernel_current:false,result});
 assert.match(node('result').innerHTML,/冻结结果已复核一致/);assert.match(node('result').innerHTML,/data-replay="accepted-event" disabled/);
 session.setItem('quant_calendar_pending','fixture');context.reset();assert.equal(session.getItem('quant_calendar_pending'),null);assert.equal(vm.runInContext('calendarItems.length',context),0);
 console.log('PASS: calendar same-ID recovery; escaped evidence; honest gaps and scope; no strategy promotion or fabricated performance; version mismatch; account reset');
})().catch(e=>{console.error(e);process.exitCode=1});
