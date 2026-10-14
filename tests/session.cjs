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
