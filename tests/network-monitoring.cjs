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
 summary:{switches:4,collected:2,failed:1,noSsh:1,links:1,linksLost:0,uplinksDown:0,problemPorts:1,poeHigh:1,cpuHigh:0,neighborIssues:1,aps:2,apsUp:1,findings:2},
 institutes:[{name:'DC',switches:1,failed:0,aps:0,apsDown:0,problems:0},{name:'Engineering',switches:2,failed:1,aps:2,apsDown:1,problems:2}],
 devices:[
  {ip:'10.0.0.1',hostname:'core-01',institute:'DC',platform:'cisco_nxos',model:'N9K-C93180YC-FX',serial:'S1',version:'10.2',uptime:'1 week',role:'core',depth:0,status:'ok',error:null,warnings:[],cpu:4,poe:null,ports:{total:54,connected:20,errDisabled:0},errDelta:0,flags:[],neighbors:[]},
  {ip:'10.0.1.1',hostname:'dsw-<img src=x onerror=alert(1)>',institute:'Engineering',platform:'cisco_ios',model:'C9300-48P',serial:'S2',version:'17.9',uptime:'2 weeks',role:'distribution',depth:1,status:'ok',error:null,warnings:[],cpu:10,poe:{availableW:740,usedW:700,pct:95},ports:{total:52,connected:30,errDisabled:1},errDelta:null,flags:['poe','err-disabled'],credentialSet:'set2',loginUser:'ro-<i>user</i>',neighborIssues:[{purpose:'cdp',command:'show cdp neighbors detail',result:'rejected',detail:'% Invalid <b>input</b>',lines:2}],commands:[{command:'terminal width 511',purpose:'session',result:'setup',detail:''},{command:'show power inline',purpose:'poe',result:'rejected',detail:'% Invalid <b>input</b>',lines:2,seconds:0.4}],neighbors:[{localPort:'Te1/1/4',name:'core-01',ip:'10.0.0.1',remotePort:'Eth1/10',platform:'N9K',via:['cdp','lldp'],kind:'switch',peer:'10.0.0.1'}]},
  {ip:'10.0.9.1',hostname:'HOSTEL_DIST_SW',institute:'Engineering',platform:'',model:'C9300-24T',serial:'',version:'',uptime:'',role:'distribution',depth:1,status:'no-ssh',error:'Does not answer SSH (tcp/22) from the collector',warnings:[],cpu:null,poe:null,ports:null,errDelta:null,flags:['no-ssh'],neighbors:[],commands:[],seenFrom:[{ip:'10.0.0.1',hostname:'core-01',port:'Eth1/11',remotePort:'Te1/1/1'}]},
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
 const fetch=async(url,opts={})=>{calls.push({url,method:opts.method||'GET',auth:opts.headers?.Authorization,body:opts.body,type:opts.headers?.['Content-Type']});
  if(url.includes('userinfo'))return {ok:true,json:async()=>({email:'net@somaiya.edu'})};
  const route=routes(url.replace(apiBase,''),opts.method||'GET',opts.headers?.Authorization);
  if(route instanceof Error)throw route;
  return {ok:route.status<300,status:route.status,json:async()=>route.body,text:async()=>route.body,blob:async()=>({})};};
 const ctx={console,window,google:window.google,Intl,Date,URL,AbortSignal,Object,Error,Set,Map,String,Number,Promise,setInterval(){},setTimeout(){},clearTimeout(){},fetch,
  document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){},createElement(){return makeEl('a')},body:{appendChild(){}}}};
 vm.createContext(ctx);vm.runInContext(script,ctx);
 return {ctx,el:id=>elements.get(id),calls,connect:async(token='fixture-token')=>{vm.runInContext('initializeGoogle();connectGoogle(false)',ctx);await callback({access_token:token,expires_in:3600});},setGrant(v){grant=v}};
}
const progressDone={startedAt:'2026-11-05T02:20:00Z',finishedAt:'2026-11-05T02:30:00Z',phase:'done',items:[
 {ip:'10.0.0.1',kind:'switch',hostname:'core-01',state:'ok',error:null,seconds:4.2,source:'core seed',at:'2026-11-05T02:21:00Z'},
 {ip:'10.0.1.3',kind:'switch',hostname:'',state:'failed',error:'SSH authentication failed <b>x</b>',seconds:9,source:'neighbour of dsw-01 Gi1/0/3',at:'2026-11-05T02:22:00Z'},
 {ip:'10.0.9.1',kind:'switch',hostname:'HOSTEL_DIST_SW',state:'no-ssh',error:'No answer on SSH (tcp/22)',seconds:null,source:'neighbour of core-01 Eth1/11',at:'2026-11-05T02:21:30Z'},
 {ip:'10.0.0.5',kind:'wlc',hostname:'412 APs',state:'ok',error:null,seconds:null,source:'WLC list in config',at:'2026-11-05T02:29:00Z'}]};
const team=(p,m)=>p==='/api/v1/me'?{status:200,body:{email:'net@somaiya.edu',canRun:true,apiVersion:2}}:p==='/api/v1/status'?{status:200,body:{run:{state:'idle',lastSuccessAt:snapshot.generatedAt},running:false,reachabilityAt:reach.checkedAt}}:p==='/api/v1/snapshot'?{status:200,body:snapshot}:p==='/api/v1/reachability'?{status:200,body:reach}:p==='/api/v1/progress'?{status:200,body:progressDone}:p==='/api/v1/run'&&m==='POST'?{status:202,body:{started:true}}:p==='/api/v1/download/topology.dot'?{status:200,body:'graph campus { "10.0.0.1" }'}:{status:404,body:{error:'Not found'}};

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
 assert.ok(h.el('port-rows').innerHTML.includes('Gi1/0/9'));
 {const nb=h.el('nb-rows').innerHTML;assert.ok(nb.includes('dsw-&lt;img')&&nb.includes('CDP')&&nb.includes('Rejected')&&nb.includes('% Invalid &lt;b&gt;input&lt;/b&gt;')&&!nb.includes('<b>input'),'Neighbour issues listed, detail escaped');
  assert.ok(nb.includes('C9300-48P')&&nb.includes('17.9')&&nb.includes('cisco_ios')&&nb.includes('set2')&&nb.includes('data-ip="10.0.1.1"'),'Neighbour issue columns');
  assert.ok(h.el('stats').innerHTML.includes('NEIGHBOUR ISSUES')&&h.el('stats').innerHTML.includes('data-go="neighbours"'),'Overview tile links to the tab');
  vm.runInContext("document.getElementById('nb-cmd').value='lldp';renderNeighbours()",h.ctx);assert.ok(h.el('nb-rows').innerHTML.includes('No neighbour issues match'),'Filter by command');
  vm.runInContext("document.getElementById('nb-cmd').value='';document.getElementById('nb-result').value='failed';renderNeighbours()",h.ctx);assert.ok(h.el('nb-count').textContent.startsWith('0 of 1'),'Filter by result');
  vm.runInContext("document.getElementById('nb-result').value='';document.getElementById('nb-inst').value='DC';renderNeighbours()",h.ctx);assert.ok(h.el('nb-count').textContent.startsWith('0 of 1'),'Filter by institute');
  vm.runInContext("document.getElementById('nb-inst').value='';snap.devices.forEach(d=>delete d.neighborIssues);renderNeighbours()",h.ctx);assert.ok(h.el('nb-rows').innerHTML.includes('class="empty"')&&h.el('nb-rows').innerHTML.includes('Listed after the next full run'),'Older snapshot shows the empty-state row');
  vm.runInContext("snap.devices[0].neighborIssues=[];renderNeighbours()",h.ctx);assert.ok(h.el('nb-rows').innerHTML.includes('No neighbour issues.'));
  vm.runInContext("setView('neighbours')",h.ctx);assert.equal(h.el('neighbours-view').classList.contains('hidden'),false);}
assert.ok(h.el('find-list').innerHTML.includes('ap-hall-09'));
 assert.equal(h.el('run-now').hidden,false,'Run now shown to the IT Network Team');assert.equal(h.el('cert-help').hidden,true,'No certificate help once the API answers');
 assert.ok(h.el('data-note').textContent.includes('Last full run'));
 vm.runInContext("document.getElementById('sw-state').value='unreachable';renderSwitches()",h.ctx);assert.ok(h.el('sw-count').textContent.startsWith('1 of 4'));
 vm.runInContext("document.getElementById('ap-state').value='down';renderAps()",h.ctx);assert.ok(h.el('ap-rows').innerHTML.includes('ap-hall-09')&&!h.el('ap-rows').innerHTML.includes('ap-lib-01'));
 vm.runInContext("openDevice('10.0.1.1')",h.ctx);assert.equal(h.el('details').open,true);assert.ok(h.el('detail-body').innerHTML.includes('Te1/1/4'));
 {const body=h.el('detail-body').innerHTML;assert.ok(body.includes('<code>show power inline</code>')&&body.includes('Rejected by the switch')&&body.includes('Sent at login'),'Commands sent to the switch are listed with their result');
  assert.ok(body.includes('% Invalid &lt;b&gt;input&lt;/b&gt;')&&!body.includes('<b>input'),'Command output text is escaped');assert.ok(body.includes('1 needs attention'));
  assert.ok(body.includes('<b>Login account</b><span>set2 · user <code>ro-&lt;i&gt;user&lt;/i&gt;</code></span>'),'The credential set and username that logged in are shown, escaped');}
 vm.runInContext("openDevice('10.0.0.1')",h.ctx);assert.ok(h.el('detail-body').innerHTML.includes('Listed after the next full run'),'Older snapshots without a command list say so');
 assert.ok(h.el('detail-body').innerHTML.includes('Login account</b><span><span class="muted">Shown after the next full run'),'Older snapshots without a login account say so');
 vm.runInContext("openDevice('10.0.1.3')",h.ctx);assert.ok(h.el('detail-body').innerHTML.includes('Listed after the next full run'));
 assert.ok(h.el('detail-body').innerHTML.includes('None worked (see Error)'),'An SSH-failed switch has no working account');
 vm.runInContext("openDevice('10.0.9.1')",h.ctx);assert.ok(!h.el('detail-body').innerHTML.includes('Login account'),'No login row for a switch without SSH');
 vm.runInContext("document.getElementById('sw-state').value='';document.getElementById('sw-search').value='set2';renderSwitches()",h.ctx);assert.ok(h.el('sw-count').textContent.startsWith('1 of 4'),'Search finds switches by credential set');
 vm.runInContext("document.getElementById('sw-search').value='';renderSwitches()",h.ctx);
 await vm.runInContext('runNow()',h.ctx);assert.ok(h.calls.some(c=>c.url===API+'/api/v1/run'&&c.method==='POST'),'Run now posts to the API');
 assert.equal(h.el('discovery-view').classList.contains('hidden'),false,'Run now opens the Discovery tab');
 // An older campus API (before single-switch refresh): no button, and the dialog says what to update.
 {const o=harness(API,(p,m)=>p==='/api/v1/me'?{status:200,body:{email:'net@somaiya.edu',canRun:true,apiVersion:1}}:team(p,m));await o.connect();
  vm.runInContext("openDevice('10.0.1.3')",o.ctx);assert.equal(o.el('refresh-switch').hidden,true,'No refresh button on an API without the endpoint');
  assert.ok(o.el('refresh-note').textContent.includes('needs the updated collector'));}
 // Per-switch progress of the last full run, and live while one runs.
 {const g=harness(API,team);await g.connect();
  assert.equal(g.el('run-line').hidden,true,'No run line when idle');assert.equal(g.el('view-progress').hidden,true);
  assert.ok(g.el('disc-status').textContent.startsWith('Last full run finished'),g.el('disc-status').textContent);
  assert.ok(g.el('progress-summary').textContent.startsWith('Last full run, switch by switch')&&g.el('progress-summary').textContent.includes('2 read · 1 SSH failed · 1 no SSH'));
  const rows=g.el('progress-rows').innerHTML;assert.ok(rows.includes('HOSTEL_DIST_SW')&&rows.includes('WLC · 412 APs')&&rows.includes('neighbour of core-01 Eth1/11'));
  assert.ok(rows.includes('&lt;b&gt;x&lt;/b&gt;')&&!rows.includes('<b>x'),'Errors are escaped');assert.ok(rows.indexOf('10.0.1.3')<rows.indexOf('core-01'),'Failures listed before successes');
  vm.runInContext("document.getElementById('progress-filter').value='no-ssh';renderProgress()",g.ctx);assert.ok(g.el('progress-rows').innerHTML.includes('HOSTEL_DIST_SW')&&!g.el('progress-rows').innerHTML.includes('core seed'),'Filter keeps only no-SSH rows');
  const startedAt='2026-11-05T03:00:00Z',liveProg={startedAt,finishedAt:null,phase:'ssh',items:[{ip:'10.0.0.1',kind:'switch',hostname:'core-01',state:'ok',seconds:3,source:'core seed',at:'2026-11-05T03:00:05Z'},{ip:'10.0.1.1',kind:'switch',hostname:'dsw-01',state:'running',source:'neighbour of core-01 Eth1/10',at:'2026-11-05T03:00:06Z'},{ip:'10.0.1.2',kind:'switch',hostname:'asw-02',state:'waiting',source:'neighbour of dsw-01 Gi1/0/1',at:'2026-11-05T03:00:06Z'}]};
  const l=harness(API,(p,m)=>p==='/api/v1/status'?{status:200,body:{run:{state:'running',phase:'ssh',startedAt,lastSuccessAt:snapshot.generatedAt},running:true,reachabilityAt:reach.checkedAt}}:p==='/api/v1/progress'?{status:200,body:liveProg}:team(p,m));await l.connect();
  assert.ok(l.el('progress-summary').textContent.startsWith('Live, switch by switch (ssh): 1 read · 0 SSH failed · 0 no SSH · 1 logging in · 1 waiting'),l.el('progress-summary').textContent);
  assert.equal(l.el('run-line').hidden,false);assert.equal(l.el('view-progress').hidden,false,'Status box links to the Discovery tab while running');
  assert.ok(l.el('disc-status').textContent.startsWith('Discovery running: ssh'));
  vm.runInContext("setView('discovery')",l.ctx);assert.equal(l.el('discovery-view').classList.contains('hidden'),false);assert.equal(l.el('view-progress').hidden,true,'No link while already on the tab');
  assert.ok(l.el('progress-rows').innerHTML.indexOf('Logging in')<l.el('progress-rows').innerHTML.indexOf('Read'),'Switches being read are listed first');
  const before=l.calls.filter(c=>c.url.endsWith('/api/v1/progress')).length;await vm.runInContext('loadAll()',l.ctx);
  assert.equal(l.calls.filter(c=>c.url.endsWith('/api/v1/progress')).length,before+1,'Progress is re-read on every poll while a run is going');
  await vm.runInContext('loadAll()',g.ctx);assert.equal(g.calls.filter(c=>c.url.endsWith('/api/v1/progress')).length,1,'and not polled when idle');}
 // Campus IP scan: counts and the "Cisco web UI only (no SSH)" list on Discovery; hidden for older snapshots.
 {const old=harness(API,team);await old.connect();assert.equal(old.el('scan-panel').hidden,true,'No scan section for a snapshot without scan counts');
  const scanned={...snapshot,scan:{subnets:3,addresses:1530,sshOpen:40,webOpen:25,ciscoSsh:4,ciscoWebOnly:1,nonCiscoSkipped:22,collected:38},webOnly:[{ip:'10.0.2.9',institute:'Engineering',ports:[443,80],hint:'Cisco <script>x</script> | cisco-IOS'}]};
  const s=harness(API,(p,m)=>p==='/api/v1/snapshot'?{status:200,body:scanned}:team(p,m));await s.connect();
  assert.equal(s.el('scan-panel').hidden,false);assert.equal(s.el('web-only-panel').hidden,false);
  const t=s.el('scan-counts').textContent;for(const x of ['1,530 addresses','40 answer SSH','25 ','4 new Cisco','1 Cisco web UI only','22 non-Cisco hosts skipped'])assert.ok(t.includes(x),x+' in '+t);
  const w=s.el('web-only-rows').innerHTML;assert.ok(w.includes('10.0.2.9')&&w.includes('Engineering')&&w.includes('443, 80'),'Web-only row: IP, institute, ports');
  assert.ok(w.includes('Cisco &lt;script&gt;x&lt;/script&gt; | cisco-IOS')&&!w.includes('<script>'),'Page title / server is escaped');
  const none=harness(API,(p,m)=>p==='/api/v1/snapshot'?{status:200,body:{...scanned,webOnly:[]}}:team(p,m));await none.connect();
  assert.equal(none.el('web-only-panel').hidden,false);assert.ok(none.el('web-only-rows').innerHTML.includes('No Cisco web UI without SSH'),'Empty list says so');
  const noList=harness(API,(p,m)=>p==='/api/v1/snapshot'?{status:200,body:{...scanned,webOnly:undefined}}:team(p,m));await noList.connect();
  assert.equal(noList.el('scan-panel').hidden,false);assert.equal(noList.el('web-only-panel').hidden,true,'No list without the field');
  // While the scan runs: one phase line with counts, not one row per address.
  const startedAt='2026-11-05T03:00:00Z',running=phase=>({status:200,body:{run:{state:'running',phase,startedAt,lastSuccessAt:snapshot.generatedAt},running:true,reachabilityAt:reach.checkedAt}});
  const sw=harness(API,(p,m)=>p==='/api/v1/status'?running('sweep'):p==='/api/v1/progress'?{status:200,body:{startedAt,finishedAt:null,phase:'sweep',sweepDone:4096,sweepTotal:12000,items:[]}}:team(p,m));await sw.connect();
  assert.ok(sw.el('progress-summary').textContent.includes('4,096 of 12,000'),sw.el('progress-summary').textContent);assert.ok(sw.el('run-note').textContent.includes('4,096 of 12,000'),sw.el('run-note').textContent);
  assert.ok(sw.el('progress-rows').innerHTML.includes('once the scan finishes'));
  const done=harness(API,(p,m)=>p==='/api/v1/status'?running('sweep'):p==='/api/v1/progress'?{status:200,body:{startedAt,finishedAt:null,phase:'sweep',sweepDone:12000,sweepTotal:12000,items:[]}}:team(p,m));await done.connect();
  assert.ok(done.el('progress-summary').textContent.includes('checking which hosts are Cisco'));
  const ssh=harness(API,(p,m)=>p==='/api/v1/status'?running('ssh'):p==='/api/v1/progress'?{status:200,body:{startedAt,finishedAt:null,phase:'ssh',sweepDone:12000,sweepTotal:12000,scan:scanned.scan,items:[{ip:'10.0.2.1',kind:'switch',hostname:'',state:'waiting',source:'IP scan (Cisco)',at:startedAt}]}}:team(p,m));await ssh.connect();
  assert.ok(ssh.el('progress-summary').textContent.includes('Scan: 4 new Cisco with SSH · 1 Cisco web UI only · 22 non-Cisco skipped'),ssh.el('progress-summary').textContent);
  assert.ok(ssh.el('progress-rows').innerHTML.includes('IP scan (Cisco)'));}
 // A neighbour switch that does not answer SSH from the collector gets its own row.
 {const n=harness(API,team);await n.connect();
  assert.ok(n.el('sw-rows').innerHTML.includes('HOSTEL_DIST_SW')&&n.el('sw-rows').innerHTML.includes('No SSH from collector'),'No-SSH switches are listed');
  assert.ok(n.el('stats').innerHTML.includes('1 no SSH'));
  vm.runInContext("document.getElementById('sw-state').value='no-ssh';renderSwitches()",n.ctx);assert.ok(n.el('sw-count').textContent.startsWith('1 of 4'));
  vm.runInContext("openDevice('10.0.9.1')",n.ctx);const body=n.el('detail-body').innerHTML;
  assert.ok(body.includes('core-01 Eth1/11 (its Te1/1/1)'),'Shows where it was seen');assert.ok(body.includes('does not answer SSH (tcp/22) from the collector'));
  assert.equal(n.el('refresh-switch').textContent,'Reconnect');assert.ok(n.el('refresh-switch').title.startsWith('Check SSH'));}
 // Refresh / reconnect one switch from its details dialog.
 {const rs={run:{state:'idle',lastSuccessAt:snapshot.generatedAt},running:false,reachabilityAt:reach.checkedAt,refresh:{},refreshing:false};
  const at='2026-11-05T04:00:00Z',refreshed={...snapshot,refreshedAt:at,devices:snapshot.devices.map(d=>d.ip==='10.0.1.3'?{...d,hostname:'asw-03',status:'ok',error:null,flags:[],refreshedAt:at,refreshedBy:'net@somaiya.edu'}:d)};
  let current=snapshot,refreshReply={status:202,body:{started:true}};
  const r=harness(API,(p,m)=>p==='/api/v1/status'?{status:200,body:{...rs}}:p==='/api/v1/snapshot'?{status:200,body:current}:p==='/api/v1/refresh'&&m==='POST'?refreshReply:team(p,m));await r.connect();
  vm.runInContext("openDevice('10.0.0.1')",r.ctx);assert.equal(r.el('refresh-switch').hidden,false,'Team members can refresh a switch');assert.equal(r.el('refresh-switch').textContent,'Refresh switch');
  vm.runInContext("openDevice('10.0.1.3')",r.ctx);assert.equal(r.el('refresh-switch').textContent,'Reconnect','An SSH-failed switch offers Reconnect');assert.equal(r.el('refresh-switch').disabled,false);
  refreshReply={status:409,body:{error:'10.0.0.1 is being refreshed. Try again when it finishes (usually under a minute).'}};
  await vm.runInContext('refreshSwitch()',r.ctx);assert.ok(r.el('toast').textContent.includes('is being refreshed'),'API refusals are shown');
  refreshReply={status:202,body:{started:true}};rs.refreshing=true;rs.refresh={state:'running',ip:'10.0.1.3',by:'net@somaiya.edu'};
  await vm.runInContext('refreshSwitch()',r.ctx);
  const post=r.calls.filter(c=>c.url===API+'/api/v1/refresh').pop();assert.equal(post.method,'POST');assert.equal(post.body,'{"ip":"10.0.1.3"}');assert.equal(post.type,'application/json');assert.equal(post.auth,'Bearer fixture-token');
  assert.equal(r.el('refresh-switch').textContent,'Refreshing…');assert.equal(r.el('refresh-switch').disabled,true);assert.ok(r.el('refresh-note').textContent.startsWith('Refreshing: logging in to this switch again'));
  vm.runInContext("openDevice('10.0.0.1')",r.ctx);assert.equal(r.el('refresh-switch').disabled,true,'One refresh at a time');assert.ok(r.el('refresh-note').textContent.includes('10.0.1.3 is being refreshed'));
  vm.runInContext("openDevice('10.0.1.3')",r.ctx);
  const snaps=()=>r.calls.filter(c=>c.url===API+'/api/v1/snapshot').length,before=snaps();
  rs.refreshing=false;rs.refresh={state:'done',ip:'10.0.1.3',by:'net@somaiya.edu',ok:true,hostname:'asw-03',finishedAt:'2026-11-05T04:00:02Z',snapshotRefreshedAt:at};current=refreshed;
  await vm.runInContext('loadAll()',r.ctx);assert.equal(snaps(),before+1,'A finished refresh reloads the snapshot');
  assert.equal(r.el('detail-title').textContent,'asw-03','The open dialog shows the refreshed switch');assert.ok(r.el('detail-body').innerHTML.includes('Last refreshed'));
  assert.equal(r.el('refresh-switch').textContent,'Refresh switch');assert.equal(r.el('refresh-switch').disabled,false);
  assert.ok(r.el('refresh-note').textContent.startsWith('Refreshed '));assert.equal(r.el('toast').textContent,'asw-03 refreshed.');
  assert.ok(r.el('data-note').textContent.includes('Last single-switch refresh'));
  await vm.runInContext('loadAll()',r.ctx);assert.equal(snaps(),before+1,'No reload once the refreshed snapshot is shown');}
 // A switch read before that stopped answering tcp/22: Down with its last-known inventory; team members can Remove it (API 4+).
 {const downDev={ip:'10.0.1.7',hostname:'asw-07',institute:'Engineering',platform:'cisco_s300',model:'SG350-28P',serial:'S7',version:'2.5.9',uptime:'',role:'access',depth:null,status:'down',error:'Stopped answering the up/down check (tcp/22) from the collector',warnings:[],cpu:null,poe:null,ports:null,errDelta:null,flags:['down'],neighbors:[],commands:[],neighborIssues:[],credentialSet:'',loginUser:'',downSince:'2026-11-05T01:15:00Z',lastSeenOk:'2026-11-04T11:00:00Z',lastUplinks:[{localPort:'Gi1/0/28',name:'dsw-<b>01</b>',ip:'10.0.1.1',remotePort:'Gi1/0/7'}]};
  const withDown={...snapshot,summary:{...snapshot.summary,switches:5,down:1},devices:[...snapshot.devices,downDev]},at='2026-11-05T05:00:00Z';
  const st={run:{state:'idle',lastSuccessAt:snapshot.generatedAt},running:false,reachabilityAt:reach.checkedAt,refresh:{},refreshing:false};
  let current=withDown,forgetReply={status:202,body:{started:true,ip:'10.0.1.7'}};
  const routes=v=>(p,m)=>p==='/api/v1/me'?{status:200,body:{email:'net@somaiya.edu',canRun:true,apiVersion:v}}:p==='/api/v1/status'?{status:200,body:{...st}}:p==='/api/v1/snapshot'?{status:200,body:current}:p==='/api/v1/forget'&&m==='POST'?forgetReply:team(p,m);
  const d=harness(API,routes(4));await d.connect();
  assert.ok(d.el('sw-rows').innerHTML.includes('asw-07')&&d.el('sw-rows').innerHTML.includes('<span class="badge bad">Down</span>'),'Down switches are listed with a Down badge');
  assert.ok(d.el('stats').innerHTML.includes('1 down'),'Overview counts Down switches');
  vm.runInContext("document.getElementById('sw-state').value='down';renderSwitches()",d.ctx);assert.ok(d.el('sw-count').textContent.startsWith('1 of 5'),'Filter Down');assert.ok(d.el('sw-rows').innerHTML.includes('asw-07'));
  vm.runInContext("document.getElementById('sw-state').value='';renderSwitches()",d.ctx);
  vm.runInContext("openDevice('10.0.1.7')",d.ctx);const body=d.el('detail-body').innerHTML;
  assert.ok(body.includes('<b>Down since</b><span>05 Nov 2026, 06:45 IST</span>'),'Down since is shown');assert.ok(body.includes('Last read'),'When it was last read');
  assert.ok(body.includes('Last-known uplinks')&&body.includes('Gi1/0/28')&&body.includes('Gi1/0/7')&&body.includes('dsw-&lt;b&gt;01')&&!body.includes('<b>01'),'Last-known uplinks, escaped');
  assert.ok(!body.includes('Login account'),'No login row for a Down switch');
  assert.equal(d.el('remove-switch').hidden,false,'Team members can Remove a Down switch');assert.equal(d.el('remove-switch').disabled,false);
  d.ctx.window.confirm=()=>false;await vm.runInContext('removeSwitch()',d.ctx);assert.ok(!d.calls.some(c=>c.url===API+'/api/v1/forget'),'Nothing is removed without confirming');
  let asked='';d.ctx.window.confirm=q=>{asked=q;return true};
  forgetReply={status:409,body:{error:'10.0.0.1 is being refreshed or removed. Try again when it finishes.'}};await vm.runInContext('removeSwitch()',d.ctx);assert.ok(d.el('toast').textContent.includes('being refreshed or removed'),'API refusals are shown');
  assert.ok(asked.includes('asw-07 (10.0.1.7)'),asked);
  forgetReply={status:202,body:{started:true,ip:'10.0.1.7'}};st.refreshing=true;st.refresh={action:'forget',state:'running',ip:'10.0.1.7',by:'net@somaiya.edu'};
  await vm.runInContext('removeSwitch()',d.ctx);
  const post=d.calls.filter(c=>c.url===API+'/api/v1/forget').pop();assert.equal(post.method,'POST');assert.equal(post.body,'{"ip":"10.0.1.7"}');assert.equal(post.type,'application/json');assert.equal(post.auth,'Bearer fixture-token');
  assert.equal(d.el('remove-switch').disabled,true);assert.ok(d.el('refresh-note').textContent.startsWith('Removing this switch'),d.el('refresh-note').textContent);
  vm.runInContext("openDevice('10.0.0.1')",d.ctx);assert.equal(d.el('remove-switch').hidden,true,'Remove only on Down rows');vm.runInContext("openDevice('10.0.1.7')",d.ctx);
  st.refreshing=false;st.refresh={action:'forget',state:'done',ip:'10.0.1.7',by:'net@somaiya.edu',ok:true,hostname:'asw-07',finishedAt:'2026-11-05T05:00:01Z',snapshotRefreshedAt:at};current={...snapshot,refreshedAt:at};
  await vm.runInContext('loadAll()',d.ctx);assert.equal(d.el('toast').textContent,'asw-07 removed from the dashboard.');assert.equal(d.el('details').open,false,'The dialog of a removed switch closes');
  assert.ok(!d.el('sw-rows').innerHTML.includes('asw-07'));
  current=withDown;const o=harness(API,routes(3));await o.connect();vm.runInContext("openDevice('10.0.1.7')",o.ctx);assert.equal(o.el('remove-switch').hidden,true,'No Remove on an API without the endpoint');
  // Between full runs: 2 missed up/down checks on a listed switch show Down too.
  const reach2={checkedAt:'2026-11-05T03:15:00Z',results:{...reach.results,'10.0.0.1':{up:false,since:'2026-11-05T02:45:00Z',latencyMs:null,misses:2}}};
  const l=harness(API,(p,m)=>p==='/api/v1/reachability'?{status:200,body:reach2}:p==='/api/v1/status'?{status:200,body:{...st,reachabilityAt:reach2.checkedAt}}:routes(4)(p,m));await l.connect();
  vm.runInContext("document.getElementById('sw-state').value='down';renderSwitches()",l.ctx);assert.ok(l.el('sw-count').textContent.startsWith('2 of 5'),l.el('sw-count').textContent);assert.ok(l.el('sw-rows').innerHTML.includes('core-01'),'A live row with 2 misses shows Down');
  vm.runInContext("document.getElementById('sw-state').value='unreachable';renderSwitches()",l.ctx);assert.ok(l.el('sw-count').textContent.startsWith('1 of 5'),'One miss is still Unreachable');
  vm.runInContext("openDevice('10.0.0.1')",l.ctx);assert.ok(l.el('detail-body').innerHTML.includes('<b>Down since</b><span>05 Nov 2026, 08:15 IST</span>'));assert.equal(l.el('remove-switch').hidden,true,'A switch read on the last run cannot be removed');
  // R5: a known switch that missed only this run's check is listed as Not answering; Down once the live check counts 2 misses.
  // R6: a Down or Not answering row whose live check answers again says so instead of Down.
  const naDev={...downDev,ip:'10.0.1.8',hostname:'asw-08',status:'unreachable',flags:['unreachable'],error:'Did not answer SSH (tcp/22) from the collector on this run',downSince:'2026-11-05T02:20:00Z'};
  current={...withDown,summary:{...withDown.summary,switches:6,unreachable:1},devices:[...withDown.devices,naDev]};
  const live=extra=>{const rc={checkedAt:'2026-11-05T03:30:00Z',results:{...reach.results,...extra}};return harness(API,(p,m)=>p==='/api/v1/reachability'?{status:200,body:rc}:p==='/api/v1/status'?{status:200,body:{...st,reachabilityAt:rc.checkedAt}}:routes(4)(p,m))};
  const rowOf=(h,name)=>h.el('sw-rows').innerHTML.split('<tr>').find(r=>r.includes('>'+name+'<'))||'';
  const na=live({});await na.connect();
  assert.ok(na.el('sw-rows').innerHTML.includes('<span class="badge bad">Not answering</span>'),'An unreachable row shows Not answering');
  assert.ok(na.el('stats').innerHTML.includes('1 not answering'));
  vm.runInContext("document.getElementById('sw-state').value='not-answering';renderSwitches()",na.ctx);assert.ok(na.el('sw-count').textContent.startsWith('1 of 6'),'Filter Not answering');
  vm.runInContext("openDevice('10.0.1.8')",na.ctx);{const b=na.el('detail-body').innerHTML;assert.ok(b.includes('<b>Not answering since</b>')&&b.includes('Last-known uplinks'),'Last-known details of a Not answering switch');}
  assert.equal(na.el('remove-switch').hidden,true,'Not answering is not Down: no Remove');
  const nd=live({'10.0.1.8':{up:false,since:'2026-11-05T02:20:00Z',latencyMs:null,misses:2}});await nd.connect();
  assert.ok(rowOf(nd,'asw-08').includes('<span class="badge bad">Down</span>'),'2 live misses turn it Down');
  vm.runInContext("openDevice('10.0.1.8')",nd.ctx);assert.equal(nd.el('remove-switch').hidden,true,'R7: no Remove until the snapshot lists it as Down');
  nd.ctx.window.confirm=()=>true;await vm.runInContext('removeSwitch()',nd.ctx);assert.ok(!nd.calls.some(c=>c.url===API+'/api/v1/forget'),'removeSwitch does nothing for it either');
  vm.runInContext("openDevice('10.0.1.7')",nd.ctx);assert.equal(nd.el('remove-switch').hidden,false,'A Down row from the snapshot still offers Remove');
  const back=live({'10.0.1.7':{up:true,since:'2026-11-05T03:30:00Z',latencyMs:2,misses:0},'10.0.1.8':{up:true,since:'2026-11-05T03:30:00Z',latencyMs:2,misses:0}});await back.connect();
  {assert.ok(rowOf(back,'asw-07').includes('Answering again (read at the next full run)')&&rowOf(back,'asw-08').includes('Answering again (read at the next full run)'),'Answering again instead of Down');}
  vm.runInContext("document.getElementById('sw-state').value='down';renderSwitches()",back.ctx);assert.ok(back.el('sw-count').textContent.startsWith('0 of 6'));
  vm.runInContext("openDevice('10.0.1.7')",back.ctx);assert.equal(back.el('remove-switch').hidden,true,'No Remove while it answers again');assert.ok(!back.el('detail-body').innerHTML.includes('Down since'));}
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

 console.log('Passed Network Monitoring fixtures: team access, escaping, reachability, filters, details, commands run, Run now, run progress, campus scan and Cisco web UI list, single-switch refresh, Down switches and Remove, DOT diagram, denial, off-campus, expiry, unconfigured API, scopes, disconnect.');
})().catch(e=>{console.error(e);process.exitCode=1});
