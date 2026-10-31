// Runs the shipped client in a VM with fixture transport; no browser credentials.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_research_ui import HTML; print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const script=html.split('<script>')[1].split('</script>')[0],nodes=new Map(),listeners=new Map();
function node(id){if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,value:'',innerHTML:'',textContent:'',disabled:false,isConnected:true,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},setAttribute(){},insertAdjacentHTML(where,s){this.innerHTML+=s},scrollIntoView(){this.scrolled=true},addEventListener(t,f){listeners.set(id+':'+t,f)}})}return nodes.get(id)}
function storage(){const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,String(v)),removeItem:k=>m.delete(k)}}
const auth=storage(),session=storage();auth.setItem('auth_token','fixture-only');let posts=0,firstRead=true;
const request={kind:'event_replay',name:'fixture',parent_id:'parent',symbols:['000333'],knowledge_mode:'as_recorded'};
const job={id:'accepted-event',status:'running',request,stage:'fixture running',progress:20,events:[]};
const response=(status,data,type='application/json')=>({ok:status>=200&&status<300,status,headers:{get:()=>type},json:async()=>data});
async function fetch(url,options={}){
 if(url.endsWith('/event-replay')){posts++;assert.equal(JSON.parse(options.body).bundle_id,'bundle');assert.equal(JSON.parse(options.body).calendar_bundle_id,'annual');return response(202,job)}
 if(url.endsWith('/experiments/accepted-event')){if(firstRead){firstRead=false;return response(502,null,'text/html')}return response(200,{...job,status:'failed',stage:'fixture terminal'})}
 if(url.endsWith('/experiments')||url.endsWith('/paper'))return response(200,{items:[]});
 if(url.endsWith('/evidence'))return response(200,{items:[]});
 throw Error('unexpected fixture request '+url);
}
const document={body:node('body'),hidden:false,getElementById:node,querySelector:()=>node('main'),addEventListener(t,f){listeners.set('document:'+t,f)}};
const context={document,localStorage:auth,sessionStorage:session,fetch,AbortController,Date,JSON,Number,String,Object,Set,Promise,console,crypto:{randomUUID:()=> 'frozen-request-key'},setTimeout:()=>1,clearTimeout(){},window:{innerWidth:390,addEventListener(){},matchMedia:()=>({matches:false})}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 await new Promise(setImmediate);node('eventBundle').value='bundle';node('eventMode').value='as_recorded';node('calendarBundle').value='annual';
 const button={dataset:{event:'parent'},disabled:false,textContent:'重放',isConnected:true};
 await assert.rejects(context.startEventReplay(button),/服务暂时繁忙/);
 assert.equal(posts,1);assert.equal(JSON.parse(session.getItem('quant_event_pending')).job_id,'accepted-event');assert.equal(button.disabled,false);
 await context.startEventReplay(button);assert.equal(posts,1,'retry GETs accepted event, never POSTs another job');assert.equal(session.getItem('quant_event_pending'),null);
 vm.runInContext(`calendarItems=[{id:'annual',years:[2024],label:'annual'}];calendarError='';evidenceItems=[{id:'bundle',label:'<img src=x onerror=alert(1)>',symbols:{'000333':1},event_count:1}];renderEvidenceForm({id:'parent',request:{symbols:['000333'],start_date:'2024-01-02',end_date:'2024-12-31'}})`,context);
 assert.match(node('result').innerHTML,/&lt;img/);assert.doesNotMatch(node('result').innerHTML,/<img src=x/);
 const result={state:{book:{paid_gross:0,tax_assessed:0},paused:true},evidence:{symbols:{'000333':1},label:'fixture',source_count:1,fingerprint:'evidence'},curve:[{date:'2024-01-01',return_pct:0},{date:'2024-01-02',return_pct:-1}],metrics:{return_pct:-1,max_drawdown_pct:-1},fills:[],source_dataset_digest:'source'};
 context.renderEvent({...job,status:'completed',kernel:'fixture',kernel_current:true,result});
 assert.match(node('result').innerHTML,/抽样资料 · 禁止晋级/);assert.doesNotMatch(node('result').innerHTML,/data-promote/);assert.doesNotMatch(node('result').innerHTML,/data-chart="holdout"/);
 assert.match(node('result').innerHTML,/最终留出未重新评估/);
 context.renderEvent({...job,request:{...request,verification_of:'original'},status:'completed',kernel:'fixture',kernel_current:false,result});
 assert.match(node('result').innerHTML,/冻结事件重放核对一致/);assert.match(node('result').innerHTML,/data-replay="accepted-event" disabled/);
 vm.runInContext(`calendarItems=[];renderEvidenceForm({id:'parent',request:{symbols:['000333'],start_date:'2024-01-02',end_date:'2024-12-31'}})`,context);
 assert.match(node('result').innerHTML,/data-event="parent" disabled/);
 context.renderEventCalendarFailure({...job,quality:{annual_plan:{symbols:{'000333':{missing_planned_sessions:['2024-03-12'],unexpected_sessions:[]}}}}});
 assert.match(node('result').innerHTML,/2024-03-12/);assert.match(node('result').innerHTML,/尚未生成事件订单/);
 console.log('PASS: mandatory annual calendar; failed date details; event recovery uses same ID; escaped evidence; no promotion or invented holdout; version mismatch disables verification');
})().catch(e=>{console.error(e);process.exitCode=1});
