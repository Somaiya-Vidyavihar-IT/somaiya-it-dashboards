// Fixture-only checks for the Network Monitoring page. No Google or campus API is contacted;
// the snapshot below is invented and contains no real hosts.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..');const html=fs.readFileSync(path.join(root,'site/network-monitoring/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];new Function(script);

assert.ok(!/localStorage|sessionStorage|indexedDB|document\.cookie/.test(script),'Network page keeps tokens and data in memory only');
assert.ok(script.includes("'Bearer '+accessToken"),'API calls carry the shared Google token');
assert.ok(!/<button(?![^>]*type="button")/.test(html),'Buttons declare type="button"');

const API='https://netmon.test:8443';
const snapshot={schema:1,generatedAt:'2026-11-05T02:30:00Z',trigger:'scheduled',triggeredBy:'',
 summary:{switches:3,collected:2,failed:1,links:1,linksLost:0,uplinksDown:0,problemPorts:1,poeHigh:1,cpuHigh:0,aps:2,apsUp:1,findings:2},
 institutes:[{name:'DC',switches:1,failed:0,aps:0,apsDown:0,problems:0},{name:'Engineering',switches:2,failed:1,aps:2,apsDown:1,problems:2}],
 devices:[
  {ip:'10.0.0.1',hostname:'core-01',institute:'DC',platform:'cisco_nxos',model:'N9K-C93180YC-FX',serial:'S1',version:'10.2',uptime:'1 week',role:'core',depth:0,status:'ok',error:null,warnings:[],cpu:4,poe:null,ports:{total:54,connected:20,errDisabled:0},errDelta:0,flags:[],neighbors:[]},
  {ip:'10.0.1.1',hostname:'dsw-<img src=x onerror=alert(1)>',institute:'Engineering',platform:'cisco_ios',model:'C9300-48P',serial:'S2',version:'17.9',uptime:'2 weeks',role:'distribution',depth:1,status:'ok',error:null,warnings:[],cpu:10,poe:{availableW:740,usedW:700,pct:95},ports:{total:52,connected:30,errDisabled:1},errDelta:null,flags:['poe','err-disabled'],commands:[{command:'terminal width 511',purpose:'session',result:'setup',detail:''},{command:'show power inline',purpose:'poe',result:'rejected',detail:'% Invalid <b>input</b>',lines:2,seconds:0.4}],neighbors:[{localPort:'Te1/1/4',name:'core-01',ip:'10.0.0.1',remotePort:'Eth1/10',platform:'N9K',via:['cdp','lldp'],kind:'switch',peer:'10.0.0.1'}]},
  {ip:'10.0.1.3',hostname:'',institute:'Engineering',platform:'',model:'',serial:'',version:'',uptime:'',role:'unknown',depth:null,status:'failed',error:'SSH authentication failed',warnings:[],cpu:null,poe:null,ports:null,errDelta:null,flags:['ssh-failed'],neighbors:[]}],
 links:[{a:'10.0.0.1',aPort:'Eth1/10',b:'10.0.1.1',bPort:'Te1/1/4',via:['cdp','lldp'],both:true,status:'up',uplink:true,downstream:'10.0.1.1',aName:'core-01',bName:'dsw-01'}],
 problemPorts:[{ip:'10.0.1.1',hostname:'dsw-01',institute:'Engineering',port:'Gi1/0/9',issue:'err-disabled',detail:'Port is err-disabled',neighbor:''}],
 aps:[{name:'ap-lib-01',institute:'Engineering',model:'515',serial:'CNTEST0001',mac:'20:4c:03:00:00:01',ip:'10.60.0.11',group:'campus',status:'Up 3d',clients:12,wlc:'10.0.0.5',switch:'asw-02',switchIp:'10.0.1.2',port:'Gi1/0/1',poeW:12.4,poeClass:'4',seenBy:['ap-lldp','lldp','wlc']},
      {name:'ap-hall-09',institute:'Engineering',model:'303',serial:'CNTEST0009',mac:'',ip:'10.60.0.19',group:'campus',status:'Down',clients:null,wlc:'10.0.0.5',switch:'',switchIp:'',port:'',poeW:null,poeClass:'',seenBy:['wlc']}],
 findings:[{severity:'warning',type:'ssh-failed',ip:'10.0.1.3',hostname:'',message:'SSH collection failed'},{severity:'warning',type:'ap-down',ip:'',hostname:'',message:'AP ap-hall-09 is Down on the WLC'}]};
const reach={checkedAt:'2026-11-05T03:00:00Z',results:{'10.0.0.1':{up:true,since:'2026-11-01T00:00:00Z',latencyMs:1},'10.0.1.1':{up:false,since:'2026-11-05T02:45:00Z',latencyMs:null}}};

function makeEl(id){return {id,value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,title:'',open:false,dataset:{},
 classList:{set:new Set(),toggle(c,on){on===undefined?(this.set.has(c)?this.set.delete(c):this.set.add(c)):on?this.set.add(c):this.set.delete(c)},add(c){this.set.add(c)},remove(c){this.set.delete(c)},contains(c){return this.set.has(c)}},
 listeners:{},addEventListener(e,fn){this.listeners[e]=fn},style:{},clientWidth:800,clientHeight:500,replaceChildren(...k){this.children=k},appendChild(k){(this.children||=[]).push(k)},querySelectorAll(){return[]},setAttribute(){},removeAttribute(){},showModal(){this.open=true},close(){this.open=false}};}
function harness(apiBase,routes){
 const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],makeEl(m[1])]));
 const calls=[];let callback,grant=true;
 const oauth={initTokenClient(opts){assert.ok(opts.scope.includes('spreadsheets.readonly')&&opts.scope.includes('userinfo.email'));callback=opts.callback;return {requestAccessToken(){}}},hasGrantedAllScopes(){return grant}};
 const window={location:{origin:'http://127.0.0.1:8765'},SOMAIYA_CONFIG:{googleClientId:'fixture.apps.googleusercontent.com',netmonApiBase:apiBase},google:{accounts:{oauth2:oauth}},addEventListener(){}};
 const fetch=async(url,opts={})=>{calls.push({url,method:opts.method||'GET',auth:opts.headers?.Authorization});
  if(url.includes('userinfo'))return {ok:true,json:async()=>({email:'net@somaiya.edu'})};
  const route=routes(url.replace(apiBase,''),opts.method||'GET',opts.headers?.Authorization);
  if(route instanceof Error)throw route;
  return {ok:route.status<300,status:route.status,json:async()=>route.body,text:async()=>route.body,blob:async()=>({})};};
 const ctx={console,window,google:window.google,Intl,Date,URL,AbortSignal,Object,Error,Set,Map,String,Number,Promise,setInterval(){},setTimeout(){},clearTimeout(){},fetch,
  document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){},createElement(){return makeEl('a')},body:{appendChild(){}}}};
 vm.createContext(ctx);vm.runInContext(script,ctx);
 return {ctx,el:id=>elements.get(id),calls,connect:async(token='fixture-token')=>{vm.runInContext('initializeGoogle();connectGoogle(false)',ctx);await callback({access_token:token,expires_in:3600});},setGrant(v){grant=v}};
}
const team=(p,m)=>p==='/api/v1/me'?{status:200,body:{email:'net@somaiya.edu',canRun:true,apiVersion:1}}:p==='/api/v1/status'?{status:200,body:{run:{state:'idle',lastSuccessAt:snapshot.generatedAt},running:false,reachabilityAt:reach.checkedAt}}:p==='/api/v1/snapshot'?{status:200,body:snapshot}:p==='/api/v1/reachability'?{status:200,body:reach}:p==='/api/v1/run'&&m==='POST'?{status:202,body:{started:true}}:p==='/api/v1/download/topology.dot'?{status:200,body:'graph campus { "10.0.0.1" }'}:{status:404,body:{error:'Not found'}};

(async()=>{
 // Team member: data loads, untrusted device text is escaped, Run now is offered.
 let h=harness(API,team);await h.connect();
 assert.equal(h.el('account-email').textContent,'net@somaiya.edu');
 assert.ok(h.calls.filter(c=>c.url.startsWith(API)).every(c=>c.auth==='Bearer fixture-token'),'Every API call is authorised');
 assert.ok(h.el('sw-rows').innerHTML.includes('dsw-&lt;img src=x onerror=alert(1)&gt;'),'Hostnames are escaped');
 assert.ok(!h.el('sw-rows').innerHTML.includes('<img'),'No device text becomes markup');
 assert.ok(h.el('stats').innerHTML.includes('UNREACHABLE NOW'));assert.ok(h.el('sw-rows').innerHTML.includes('Unreachable'),'Reachability overrides last-run state');
 assert.ok(h.el('sw-rows').innerHTML.includes('SSH failed'));
 assert.ok(h.el('ap-rows').innerHTML.includes('CNTEST0001')&&h.el('ap-rows').innerHTML.includes('Not up')===false);
 assert.ok(h.el('topo').innerHTML.includes('core-01')&&h.el('topo').innerHTML.includes('Not linked to the core'));
 assert.ok(h.el('port-rows').innerHTML.includes('Gi1/0/9'));assert.ok(h.el('find-list').innerHTML.includes('ap-hall-09'));
 assert.equal(h.el('run-now').hidden,false,'Run now shown to the IT Network Team');assert.equal(h.el('cert-help').hidden,true,'No certificate help once the API answers');
 assert.ok(h.el('data-note').textContent.includes('Last full run'));
 vm.runInContext("document.getElementById('sw-state').value='unreachable';renderSwitches()",h.ctx);assert.ok(h.el('sw-count').textContent.startsWith('1 of 3'));
 vm.runInContext("document.getElementById('ap-state').value='down';renderAps()",h.ctx);assert.ok(h.el('ap-rows').innerHTML.includes('ap-hall-09')&&!h.el('ap-rows').innerHTML.includes('ap-lib-01'));
 vm.runInContext("openDevice('10.0.1.1')",h.ctx);assert.equal(h.el('details').open,true);assert.ok(h.el('detail-body').innerHTML.includes('Te1/1/4'));
 {const body=h.el('detail-body').innerHTML;assert.ok(body.includes('<code>show power inline</code>')&&body.includes('Rejected by the switch')&&body.includes('Sent at login'),'Commands sent to the switch are listed with their result');
  assert.ok(body.includes('% Invalid &lt;b&gt;input&lt;/b&gt;')&&!body.includes('<b>input'),'Command output text is escaped');assert.ok(body.includes('1 needs attention'));}
 vm.runInContext("openDevice('10.0.0.1')",h.ctx);assert.ok(h.el('detail-body').innerHTML.includes('Listed after the next full run'),'Older snapshots without a command list say so');
 vm.runInContext("openDevice('10.0.1.3')",h.ctx);assert.ok(h.el('detail-body').innerHTML.includes('Listed after the next full run'));
 await vm.runInContext('runNow()',h.ctx);assert.ok(h.calls.some(c=>c.url===API+'/api/v1/run'&&c.method==='POST'),'Run now posts to the API');
 // Topology diagram: the API's DOT file is laid out by Viz.js (stubbed here) and active SVG content is removed.
 {const removed=[],script={remove(){removed.push('script')}},title={textContent:'10.0.0.1'};
  const node={attributes:[{name:'xlink:href'},{name:'class'}],dataset:{},removeAttribute(n){removed.push(n)},querySelector:s=>s==='title'?title:null};
  const svg={viewBox:{baseVal:{width:400,height:200}},attributes:[{name:'onload'}],setAttribute(){},removeAttribute(n){removed.push(n)},
   querySelectorAll:s=>s==='script,foreignObject'?[script]:s==='*'?[node]:s==='g.node'?[node]:s==='g.edge'?[{}]:[]};
  let laidOut='';h.ctx.window.Viz={instance:async()=>({renderSVGElement(text){laidOut=text;return svg}})};
  vm.runInContext("setView('topology');setTopoMode('dot')",h.ctx);for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
  assert.equal(h.el('topo-dot-view').hidden,false);assert.equal(h.el('topo-tree-view').hidden,true);
  assert.ok(h.calls.some(c=>c.url===API+'/api/v1/download/topology.dot'&&c.auth==='Bearer fixture-token'),'Diagram reads the DOT file from the API with the Google token');
  assert.ok(laidOut.startsWith('graph campus'),'The DOT file text is what gets laid out');
  assert.deepEqual(removed.sort(),['onload','script','xlink:href'],'Scripts, event handlers and links are stripped from the SVG');
  assert.equal(node.dataset.ip,'10.0.0.1','Nodes open switch details');assert.ok(h.el('dot-note').textContent.startsWith('1 switch · 1 link'));
  assert.ok(h.el('dot-canvas').children[0].style.transform.includes('scale('),'Diagram is fitted to the canvas');
  vm.runInContext("setTopoMode('tree')",h.ctx);assert.equal(h.el('topo-tree-view').hidden,false);vm.runInContext("setTopoMode('dot')",h.ctx);}
 vm.runInContext('disconnectGoogle()',h.ctx);assert.equal(h.el('account-email').textContent,'Not signed in');assert.ok(!h.el('sw-rows').innerHTML.includes('core-01'),'Disconnect clears network data');assert.equal(h.el('run-now').hidden,true);
 assert.ok(h.el('dot-canvas').innerHTML.includes('No discovery data'),'Disconnect clears the diagram');

 // Signed in but not on the IT Network Team list: nothing is shown.
 h=harness(API,(p)=>p==='/api/v1/me'?{status:403,body:{error:'This account is not on the IT Network Team list for the network dashboard.'}}:team(p));await h.connect();
 assert.ok(h.el('data-note').textContent.includes('IT Network Team list'));assert.equal(h.el('data-label').textContent,'Campus network API · Access denied');
 assert.equal(h.el('run-now').hidden,true);assert.ok(!h.calls.some(c=>c.url.endsWith('/api/v1/snapshot')),'Snapshot never requested when denied');assert.ok(h.el('sw-rows').innerHTML.includes('No data'));

 // Off campus: the API cannot be reached.
 h=harness(API,()=>new TypeError('Failed to fetch'));await h.connect();assert.ok(h.el('data-note').textContent.includes('only on the campus network'));
 assert.equal(h.el('cert-help').hidden,false,'Self-signed certificate help shown when the API cannot be reached');assert.equal(h.el('cert-link').href,API+'/api/v1/health');

 // Google token rejected by the API: treated as expiry.
 h=harness(API,()=>({status:401,body:{error:'Google authorization is invalid or expired. Connect with Google again.'}}));await h.connect();
 assert.equal(h.el('account-email').textContent,'Not signed in');assert.ok(h.el('data-note').textContent.includes('expired'));

 // API address not configured yet.
 h=harness('',team);await h.connect();assert.ok(h.el('data-note').textContent.includes('netmonApiBase'));assert.equal(h.el('data-label').textContent,'Campus network API · Not configured');

 // Missing Google scopes.
 h=harness(API,team);h.setGrant(false);await h.connect();assert.ok(h.el('data-note').textContent.includes('grant'));assert.equal(h.el('account-email').textContent,'Not signed in');

 console.log('Passed Network Monitoring fixtures: team access, escaping, reachability, filters, details, commands run, Run now, DOT diagram, denial, off-campus, expiry, unconfigured API, scopes, disconnect.');
})().catch(e=>{console.error(e);process.exitCode=1});
