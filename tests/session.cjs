const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..');let now=1000,timer;
const context={Date:{now:()=>now},Set,Number,setTimeout(fn){timer=fn;return 1},clearTimeout(){timer=null}};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(root,'site/shared/session-worker.js'),'utf8'),context);
const port=()=>({messages:[],postMessage(v){this.messages.push(structuredClone(v));},start(){},close(){}});
const a=port(),b=port();context.onconnect({ports:[a]});context.onconnect({ports:[b]});
a.onmessage({data:{type:'set',session:{accessToken:'fixture-session-token',expiresAt:2000,email:'fixture@example.com'}}});
assert.equal(b.messages.at(-1).session.email,'fixture@example.com');
const c=port();context.onconnect({ports:[c]});assert.equal(c.messages.at(-1).session.accessToken,'fixture-session-token');
b.onmessage({data:{type:'clear'}});assert.equal(a.messages.at(-1).session.accessToken,'');assert.equal(c.messages.at(-1).session.accessToken,'');
a.onmessage({data:{type:'set',session:{accessToken:'expired-fixture',expiresAt:500,email:'fixture@example.com'}}});assert.equal(c.messages.at(-1).session.accessToken,'');
a.onmessage({data:{type:'set',session:{accessToken:'fixture-session-token',expiresAt:2000,email:'fixture@example.com'}}});now=2001;timer();assert.equal(b.messages.at(-1).session.accessToken,'');assert.match(b.messages.at(-1).session.reason,/expired/);
for(const name of ['production-support','koha-support']){const html=fs.readFileSync(path.join(root,'site',name,'index.html'),'utf8');assert.ok(html.includes('shared/session.js'));assert.ok(html.includes('SomaiyaSession?.subscribe'));assert.ok(html.includes('SomaiyaSession?.clear'));assert.ok(!/localStorage|sessionStorage|client_secret/.test(html));new Function(html.match(/<script>([\s\S]*?)<\/script>/)[1]);}
const hub=fs.readFileSync(path.join(root,'site/index.html'),'utf8');assert.ok(hub.includes('dashboard-frame'));assert.ok(hub.includes('hub-disconnect'));new Function(hub.match(/<script>([\s\S]*?)<\/script>/)[1]);
console.log('Passed: shared session handoff, new-tab adoption, global logout, expired-token rejection, expiry propagation, and dashboard integration.');
{
 const channels=[];
 class Channel{constructor(name){this.name=name;channels.push(this);}postMessage(data){for(const channel of channels)if(channel!==this&&channel.name===this.name)channel.onmessage?.({data:structuredClone(data)});}}
 function client(parent){const win={location:{origin:'https://fixture.example'},addEventListener(){}};win.parent=parent||win;const c={window:win,document:{currentScript:{src:'https://fixture.example/shared/session.js?v=2'}},URL,Date,Set,Number,BroadcastChannel:Channel,SharedWorker:class{constructor(){throw Error('Fixture fallback');}},setTimeout(){return 1},clearTimeout(){}};vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(root,'site/shared/session.js'),'utf8'),c);return win;}
 const first=client();first.SomaiyaSession.set({accessToken:'fixture-session-token',email:'fixture@example.com',expiresAt:Date.now()+60000});
 const second=client();let adopted;second.SomaiyaSession.subscribe(s=>adopted=s);assert.equal(adopted.email,'fixture@example.com');
 const embedded=client(first);assert.equal(embedded.SomaiyaSession,first.SomaiyaSession);
 let loggedOut;first.SomaiyaSession.subscribe(s=>loggedOut=s);second.SomaiyaSession.clear();assert.equal(loggedOut.accessToken,'');
 console.log('Passed: newly loaded page recovery, embedded portal session ownership, and cross-tab logout fallback.');
}

{
 // Live and /dev/ preview deployments share an origin; their session channels must not.
 const src=require('node:fs').readFileSync(require('node:path').join(__dirname,'../site/shared/session.js'),'utf8');
 assert.ok(src.includes("sessionName='somaiya-dashboard-session-v1:'+base.pathname"));
 assert.ok(src.includes('new BroadcastChannel(sessionName)'));assert.ok(src.includes('name:sessionName'));
 console.log('Passed: session channel scoped to the deployment path.');
}

{
 // One-click reconnect remembers only the account email, never the token, and Disconnect forgets it.
 const store=new Map(),localStorage={getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)};
 class Channel{postMessage(){}}
 const load=()=>{const win={location:{origin:'https://fixture.example'},addEventListener(){}};win.parent=win;const c={window:win,localStorage,document:{currentScript:{src:'https://fixture.example/shared/session.js?v=2'}},URL,Date,Set,Number,BroadcastChannel:Channel,SharedWorker:class{constructor(){throw Error('Fixture fallback');}},setTimeout(){return 1},clearTimeout(){}};vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(root,'site/shared/session.js'),'utf8'),c);return win.SomaiyaSession;};
 const first=load();assert.equal(first.lastAccount(),'');
 first.set({accessToken:'fixture-session-token',email:'fixture@example.com',expiresAt:Date.now()+60000});
 assert.ok(![...store.values()].some(v=>v.includes('fixture-session-token')));
 const reloaded=load();assert.equal(reloaded.lastAccount(),'fixture@example.com');
 reloaded.clear('Google authorization expired. Connect with Google again.');assert.equal(reloaded.lastAccount(),'fixture@example.com');
 reloaded.forget();assert.equal(load().lastAccount(),'');
 for(const name of ['production-support','koha-support','uat-observations']){const html=fs.readFileSync(path.join(root,'site',name,'index.html'),'utf8');assert.ok(html.includes("{prompt:'',login_hint:hint}"));assert.ok(html.includes("$('settings-connect').onclick=()=>connectGoogle(true)"));assert.ok(html.includes('function disconnectGoogle(){window.SomaiyaSession?.forget?.();'));}
 assert.ok(fs.readFileSync(path.join(root,'site/index.html'),'utf8').includes('SomaiyaSession?.forget?.()'));
 console.log('Passed: one-click reconnect remembers only the account email, survives expiry, and Disconnect forgets it.');
}
