// Fixture-only checks for the CCTV Monitoring page. No Google, campus API or SolarWinds is contacted;
// the snapshot below is invented (documentation addresses) and contains no real cameras.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..');const html=fs.readFileSync(path.join(root,'site/cctv-monitoring/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];new Function(script);

assert.ok(!/localStorage|sessionStorage|indexedDB|document\.cookie/.test(script),'CCTV page keeps tokens and data in memory only');
assert.ok(script.includes("Authorization:'Bearer '+accessToken"),'API calls carry the shared Google token');
assert.ok(!/<button(?![^>]*type="button")/.test(html),'Buttons declare type="button"');
assert.ok(!/\/api\/v1\/(me|status|snapshot|run|refresh)'/.test(script),'The CCTV page only calls the CCTV routes');

const API='https://netmon.test:8443';
const dev=(o)=>Object.assign({kind:'camera',matchedBy:'range',location:'',vendor:'Unknown',model:'Unknown',type:'',status:'up',statusText:'',downSince:null,downSinceExact:null,downSinceSource:null,downHours:null,escalated:false},o);
const snapshot={schema:1,generatedAt:'2026-11-10T06:00:00Z',nodesRead:2847,
 summary:{cameras:{total:3,up:1,warning:0,down:2,unmonitored:0,unknown:0,escalated:1},nvrs:{total:2,up:1,warning:0,down:1,unmonitored:0,unknown:0,escalated:1},escalated:2,notInSolarWinds:1},
 institutes:[{name:'Engineering',cameras:3,camerasDown:2,nvrs:1,nvrsDown:1,escalated:2},{name:'Main Gate',cameras:0,camerasDown:0,nvrs:1,nvrsDown:0,escalated:0}],
 devices:[
  dev({id:1,kind:'nvr',matchedBy:'name',name:'ENG-NVR-1',ip:'192.0.2.1',institute:'Engineering',status:'down',downSince:'2026-11-01T06:00:00Z',downSinceExact:true,downSinceSource:'event',downHours:216,escalated:true}),
  dev({id:2,kind:'nvr',matchedBy:'tag',name:'GATE-NVR',ip:'192.0.2.2',institute:'Main Gate',vendor:'net-snmp',model:'Linux',type:'Dahua NVR'}),
  dev({id:3,name:'Lift cam <img src=x onerror=alert(1)>',ip:'192.0.2.10',institute:'Engineering',matchedBy:'tag',type:'CCTV'}),
  dev({id:4,name:'192.0.2.11',ip:'192.0.2.11',institute:'Engineering',status:'down',downSince:'2026-11-09T20:00:00Z',downSinceExact:true,downSinceSource:'event',downHours:10,escalated:false}),
  dev({id:5,name:'=HYPERLINK("http://x")',ip:'192.0.2.12',institute:'Engineering',status:'down',downSince:'2026-10-09T18:33:02Z',downSinceExact:false,downSinceSource:'before-events',downHours:755.4,escalated:true})],
 notInSolarWinds:[{ip:'198.51.100.7',mac:'AA-BB',vendor:'Zhejiang Dahua <b>x</b>',name:'NVR.example',lastSeen:'2026-11-09T22:40:47Z'}],notInSolarWindsAt:'2026-11-10T06:00:00Z',notInSolarWindsError:null,
 rules:{typeProperty:'Device_Type',cameraTypes:['camera','cctv'],nvrTypes:['dahua nvr','nvr'],nvrNamePattern:'(?<![A-Za-z])NVR(?![A-Za-z])',cameraRanges:['192.0.2.0/25'],ipamRanges:['198.51.100.0/24'],escalateHours:48,pollMinutes:5}};

function makeEl(id){return {id,value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,title:'',open:false,dataset:{},
 classList:{set:new Set(),toggle(c,on){on===undefined?(this.set.has(c)?this.set.delete(c):this.set.add(c)):on?this.set.add(c):this.set.delete(c)},add(c){this.set.add(c)},remove(c){this.set.delete(c)},contains(c){return this.set.has(c)}},
 listeners:{},addEventListener(e,fn){this.listeners[e]=fn},style:{},querySelectorAll(){return[]},setAttribute(){},removeAttribute(){},click(){},remove(){}};}
function harness(apiBase,routes){
 const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],makeEl(m[1])]));
 const calls=[],blobs=[];let callback;
 const oauth={initTokenClient(opts){assert.ok(opts.scope.includes('spreadsheets.readonly')&&opts.scope.includes('userinfo.email'));callback=opts.callback;return {requestAccessToken(){}}},hasGrantedAllScopes(){return true}};
 const window={location:{origin:'http://127.0.0.1:8765'},SOMAIYA_CONFIG:{googleClientId:'fixture.apps.googleusercontent.com',netmonApiBase:apiBase},google:{accounts:{oauth2:oauth}},addEventListener(){}};
 const fetch=async(url,opts={})=>{calls.push({url,method:opts.method||'GET',auth:opts.headers?.Authorization});
  if(url.includes('userinfo'))return {ok:true,json:async()=>({email:'guard@somaiya.edu'})};
  const route=routes(url.replace(apiBase,''),opts.method||'GET');
  if(route instanceof Error)throw route;
  return {ok:route.status<300,status:route.status,json:async()=>route.body};};
 class FakeBlob{constructor(parts){blobs.push(parts.join(''))}}
 const ctx={console,window,google:window.google,Intl,Date,URL:{createObjectURL:()=>'blob:x',revokeObjectURL(){}},Blob:FakeBlob,AbortSignal,Object,Error,Set,Map,String,Number,Promise,Math,setInterval(){},setTimeout(){},clearTimeout(){},fetch,
  document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){},createElement(){return makeEl('a')},body:{appendChild(){}}}};
 vm.createContext(ctx);vm.runInContext(script,ctx);
 return {ctx,el:id=>elements.get(id),calls,blobs,connect:async()=>{vm.runInContext('initializeGoogle();connectGoogle(false)',ctx);await callback({access_token:'fixture-token',expires_in:3600});}};
}
const viewer=(p,m)=>p==='/api/v1/cctv/me'?{status:200,body:{email:'guard@somaiya.edu',canRefresh:true,apiVersion:3}}:p==='/api/v1/cctv/status'?{status:200,body:{poll:{state:'ok',ok:true,lastSuccessAt:snapshot.generatedAt,finishedAt:snapshot.generatedAt},polling:false,pollMinutes:5}}:p==='/api/v1/cctv/snapshot'?{status:200,body:snapshot}:p==='/api/v1/cctv/refresh'&&m==='POST'?{status:202,body:{started:true}}:{status:404,body:{error:'Not found'}};

(async()=>{
 // CCTV viewer: data loads from the CCTV routes only, device text is escaped, escalations and the IPAM gap show.
 let h=harness(API,viewer);await h.connect();
 assert.ok(h.calls.filter(c=>c.url.startsWith(API)).every(c=>c.auth==='Bearer fixture-token'&&c.url.startsWith(API+'/api/v1/cctv/')),'Only authorised CCTV routes are called');
 assert.ok(h.el('cam-rows').innerHTML.includes('Lift cam &lt;img src=x onerror=alert(1)&gt;'),'Camera names are escaped');
 assert.ok(!h.el('cam-rows').innerHTML.includes('<img'),'No device text becomes markup');
 assert.ok(h.el('stats').innerHTML.includes('CAMERAS DOWN')&&h.el('stats').innerHTML.includes('DOWN &gt; 2 DAYS'));
 assert.ok(h.el('nvr-rows').innerHTML.includes('ENG-NVR-1')&&h.el('nvr-rows').innerHTML.includes('9 d 0 h'),'NVR inventory with down duration');
 assert.ok(h.el('esc-rows').innerHTML.includes('ENG-NVR-1')&&h.el('esc-rows').innerHTML.includes('≥ 31 d'),'Escalations list, approximate duration marked');
 assert.ok(!h.el('esc-rows').innerHTML.includes('192.0.2.11'),'10 hours down is not escalated');
 assert.ok(h.el('esc-rows').innerHTML.indexOf('192.0.2.12')<h.el('esc-rows').innerHTML.indexOf('ENG-NVR-1'),'Longest outage first');
 assert.ok(h.el('cam-rows').innerHTML.includes('before '),'Down since before the oldest kept event');
 assert.ok(h.el('gap-rows').innerHTML.includes('Zhejiang Dahua &lt;b&gt;x&lt;/b&gt;'),'IPAM gap rows are escaped');
 assert.ok(h.el('data-note').textContent.includes('2847 nodes checked'));
 assert.equal(h.el('poll-now').hidden,false,'Read SolarWinds now is offered to viewers');
 // Filters.
 h.el('cam-state').value='escalated';vm.runInContext('renderCameras()',h.ctx);
 assert.equal(h.el('cam-count').textContent,'1 of 3 cameras');
 h.el('cam-state').value='';vm.runInContext('renderCameras()',h.ctx);
 // CSV: formula-looking text is neutralised and quoted.
 assert.equal(vm.runInContext("csvCell('=1+1')",h.ctx),"'=1+1");assert.equal(vm.runInContext("csvCell('-x')",h.ctx),"'-x");assert.equal(vm.runInContext(`csvCell('a,"b"')`,h.ctx),'"a,""b"""');
 vm.runInContext("downloadCsv('camera')",h.ctx);
 assert.ok(h.blobs[0].includes(`"'=HYPERLINK(""http://x"")"`),'Exported names cannot run as formulas');
 assert.ok(!h.blobs[0].includes('fixture-token'),'Exports carry no authentication');
 // Read SolarWinds now.
 await vm.runInContext('pollNow()',h.ctx);
 assert.ok(h.calls.some(c=>c.url===API+'/api/v1/cctv/refresh'&&c.method==='POST'));

 // Not on the CCTV list: data cleared, message shown, session kept.
 h=harness(API,(p)=>p==='/api/v1/cctv/me'?{status:403,body:{error:'This account is not on the CCTV dashboard list. Ask the IT Network Team to add it.'}}:viewer(p));
 await h.connect();
 assert.ok(h.el('data-note').textContent.includes('CCTV dashboard list'));assert.equal(h.el('data-label').textContent,'Campus CCTV API · Access denied');
 assert.ok(h.el('cam-rows').innerHTML.includes('No data'));assert.equal(h.el('account-email').textContent,'guard@somaiya.edu');assert.equal(h.el('poll-now').hidden,true);

 // A server from before the CCTV dashboard answers 404 Not found.
 h=harness(API,()=>({status:404,body:{error:'Not found'}}));await h.connect();
 assert.ok(h.el('data-note').textContent.includes('does not have CCTV monitoring yet'));

 // CCTV not set up on the server: its own message is shown.
 h=harness(API,()=>({status:404,body:{error:'CCTV monitoring is not set up on the campus server ([cctv] in config.toml).'}}));await h.connect();
 assert.ok(h.el('data-note').textContent.includes('not set up'));

 // Off campus: unreachable, with the certificate hint.
 h=harness(API,()=>new TypeError('Failed to fetch'));await h.connect();
 assert.ok(h.el('data-note').textContent.includes('campus network or VPN'));assert.equal(h.el('cert-help').hidden,false);

 // Expiry: 401 clears the data and the session.
 let expired=false;h=harness(API,(p,m)=>expired&&p==='/api/v1/cctv/status'?{status:401,body:{error:'Google authorization expired. Connect with Google again.'}}:viewer(p,m));
 await h.connect();assert.ok(h.el('cam-rows').innerHTML.includes('192.0.2.11'));expired=true;await vm.runInContext('loadAll(true)',h.ctx);
 assert.equal(h.el('account-email').textContent,'Not signed in');assert.ok(!h.el('cam-rows').innerHTML.includes('192.0.2.11'),'Camera data cleared on expiry');

 // Not configured.
 h=harness('',viewer);await h.connect();assert.ok(h.el('data-note').textContent.includes('not configured'));

 console.log('Passed CCTV page: CCTV routes only, escaping, escalations, IPAM gap, filters, safe CSV, Read now, access denied, old server, unreachable, expiry, not configured.');
})().catch(e=>{console.error(e);process.exitCode=1});
