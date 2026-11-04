// Executes the shipped client against a fake transport, never a real browser or account.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_research_ui import HTML; print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const script=html.split('<script>')[1].split('</script>')[0];
const nodes=new Map(),listeners=new Map();
function node(id){if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,value:'',innerHTML:'',textContent:'',innerText:'',disabled:false,isConnected:true,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,force){const next=force===undefined?!classes.has(x):force;next?classes.add(x):classes.delete(x);return next}},setAttribute(){},scrollIntoView(){this.scrolled=true},addEventListener(type,fn){listeners.set(id+':'+type,fn)}})}return nodes.get(id)}
const storage=()=>{const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)}};
const session=storage(),auth=storage();auth.setItem('auth_token','unit-test-only');
let posts=0,firstRead=true,timerId=0;const timers=new Map();
const request={name:'transport failure test',symbols:['600519'],benchmark:'000300',start_date:'2023-01-01',end_date:'2026-10-30'};
const job={id:'confirmed-job',status:'running',stage:'已受理',progress:1,request,events:[]};
const response=(status,data,type='application/json')=>({ok:status>=200&&status<300,status,headers:{get:()=>type},json:async()=>data});
async function fetch(url,options={}){
 if(url.endsWith('/experiments')&&options.method==='POST'){posts++;return response(202,job)}
 if(url.endsWith('/experiments/confirmed-job')){if(firstRead){firstRead=false;return response(502,null,'text/html')}return response(200,{...job,status:'failed',stage:'fixture terminal'})}
 if(url.endsWith('/experiments')||url.endsWith('/paper'))return response(200,{items:[]});
 throw Error('unexpected test request '+url);
}
const document={body:node('body'),hidden:false,getElementById:node,querySelectorAll:()=>[],querySelector:selector=>selector==='main'?node('main'):null,addEventListener(type,fn){listeners.set('document:'+type,fn)}};
// main.scrollIntoView is needed only at mobile size; transport assertions use desktop.
const context={document,localStorage:auth,sessionStorage:session,fetch,AbortController,Date,JSON,Number,String,Set,Promise,console,crypto:{randomUUID:()=> 'stable-test-request'},setTimeout(fn,ms){timers.set(++timerId,fn);return timerId},clearTimeout(id){timers.delete(id)}};
context.window={innerWidth:390,addEventListener(){},matchMedia:()=>({matches:false})};
for(const m of html.matchAll(/<input[^>]*id="(cfg_[^"]+)"[^>]*value="([^"]*)"/g))node(m[1]).value=m[2];node('cfg_strategy').value='momentum';
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 await new Promise(setImmediate);node('name').value=request.name;node('symbols').value='600519';node('benchmark').value='000300';node('start').value=request.start_date;node('end').value=request.end_date;
 const submit=listeners.get('researchForm:submit');assert.ok(submit);node('researchForm').classList.add('expanded');
 await submit({preventDefault(){}});
 assert.equal(posts,1);assert.equal(JSON.parse(session.getItem('quant_research_pending')).job_id,'confirmed-job');
 assert.match(node('error').textContent,/服务暂时繁忙/);assert.doesNotMatch(node('error').textContent,/Unexpected token/);
 assert.equal(node('run').disabled,false);assert.equal(node('researchForm').classList.contains('expanded'),false);
 await submit({preventDefault(){}});
 assert.equal(posts,1,'recovery must GET the accepted experiment, never POST a duplicate');
 assert.equal(session.getItem('quant_research_pending'),null);assert.match(node('result').innerHTML,/fixture terminal/);
 assert.equal(node('error').textContent,'');assert.equal(node('main').scrolled,true);
 console.log('PASS: accepted POST + HTML 502 recovers same ID, no duplicate POST, localized error, usable controls');
})().catch(e=>{console.error(e);process.exitCode=1});
