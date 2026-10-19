const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');const path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/production-support/index.html'),'utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1];new Function(code);
const ctx={window:{},Date,Set,Error};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'site/shared/sheet-data.js'),'utf8'),ctx);
const {parseDate,selectTab,mapRows}=ctx.window.SomaiyaSheet;
assert.equal(parseDate('05-10-2026'),'2026-10-05');assert.equal(parseDate('31-02-2026'),'');
const tab=selectTab([{properties:{title:'All Module Bugs, Issues',sheetId:24}}]);assert.equal(tab.sheetId,24);
assert.throws(()=>selectTab([{properties:{title:'Production Issue',sheetId:1}}]));
const mapped=mapRows([['Description','Module','Status','Date Reported','Resolution Date','Progress'],['Payment issue','Payments','Completed','05-10-2026','13-10-2026',''],['Another issue','Hostel','In Progress','12-10-2026','','50%']],tab);
assert.equal(mapped.issues[0].status,'Resolved');assert.equal(mapped.issues[0].progress,null);assert.equal(mapped.issues[1].progress,50);
assert.equal(mapped.issues[0].resolved,'2026-10-13');assert.ok(mapped.issues[0].sourceUrl.includes('gid=24'));
assert.ok(!html.includes("fetch('/api/"));assert.ok(!html.includes("fetch('/auth/"));assert.ok(!html.includes('localStorage'));assert.ok(!html.includes('sessionStorage'));assert.ok(!html.includes('client_secret'));assert.ok(!html.includes('issue-form'));
assert.ok(html.includes('spreadsheets.readonly'));assert.ok(html.includes('tokenExpiresAt'));assert.ok(html.includes('https://accounts.google.com/gsi/client'));
assert.ok(fs.existsSync(path.join(root,'site/assets/somaiya-university.svg')));
console.log('Passed: browser syntax, selected-tab mapping, dates, progress, Pages paths, read-only scope, and no backend or persistent token storage.');

// KOHA report regression checks.
{
// Fixture-only browser logic checks; these do not call Google or read real issues.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..');const html=fs.readFileSync(path.join(root,'site/koha-support/index.html'),'utf8');
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',innerHTML:'',classList:{toggle(){},add(){},remove(){}},querySelectorAll(){return[]},addEventListener(){},showModal(){},close(){}}]));
let callback,requested=false,fail=false,grant=true;
const oauth={initTokenClient(opts){assert.ok(opts.scope.includes('spreadsheets.readonly'));callback=opts.callback;return {requestAccessToken(){requested=true}}},hasGrantedAllScopes(){return grant}};
const window={location:{origin:'http://127.0.0.1:8765'},SOMAIYA_CONFIG:{googleClientId:'fixture.apps.googleusercontent.com'},google:{accounts:{oauth2:oauth}},addEventListener(){}};
const ctx={console,window,google:window.google,Intl,Date,URL,Blob,AbortSignal,setInterval(){},setTimeout(){},clearTimeout(){},document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){}},fetch:async(url,opts)=>{assert.ok(opts.headers.Authorization.startsWith('Bearer '));if(fail)throw Error('Fixture network failure');return {ok:true,json:async()=>url.includes('userinfo')?{email:'fixture@example.com'}:url.includes('/values/')?{values:[['Timestamp','Case ID','Test','Brief Description of the Issue','Issue Category','Assigned To','Status','Is it a repetitive issue?'],[46308,'CASE-1','Fixture Library','Fixture issue','Book Issue/Return','Fixture Owner','In Progress','Yes'],[46309,'CASE-2','Fixture Library','Resolved fixture','Cataloguing','','Resolved','No'],[46309,'','Fixture Library','Triage fixture','Login','','','']]}:{sheets:[{properties:{title:'Form Responses 1',sheetId:122111389}}]}}}};
vm.createContext(ctx);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
(async()=>{vm.runInContext('initializeGoogle();connectGoogle()',ctx);assert.equal(requested,true);await callback({access_token:'fixture-access-token',expires_in:3600});assert.equal(elements.get('account-email').textContent,'fixture@example.com');assert.ok(elements.get('issue-rows').innerHTML.includes('Fixture issue'));assert.ok(elements.get('stats').innerHTML.includes('Recurring pending'));assert.ok(elements.get('issue-rows').innerHTML.includes('Needs triage'));assert.ok(elements.get('report-warning').textContent.includes('Resolution Date'));assert.equal(vm.runInContext('SomaiyaSheet.parseDate(46308)',ctx),'2026-10-13');assert.equal(vm.runInContext("SomaiyaSheet.parseDate('6/19/2026 15:18:12')",ctx),'2026-06-19');assert.equal(vm.runInContext("SomaiyaSheet.parseDate('2/31/2026')",ctx),'');assert.throws(()=>vm.runInContext("SomaiyaSheet.selectTab([{properties:{title:'Sheet1'}}])",ctx));vm.runInContext("tab='repeat';renderTable()",ctx);assert.ok(!elements.get('issue-rows').innerHTML.includes('Triage fixture'));vm.runInContext("tab='open';renderTable()",ctx);
fail=true;await vm.runInContext('refreshSheet()',ctx);assert.ok(elements.get('data-note').textContent.includes('stale'));assert.ok(elements.get('issue-rows').innerHTML.includes('Fixture issue'));
vm.runInContext('tokenExpiresAt=0',ctx);await vm.runInContext('refreshSheet()',ctx);assert.equal(elements.get('account-email').textContent,'Not signed in');assert.ok(!elements.get('issue-rows').innerHTML.includes('Fixture issue'));assert.ok(elements.get('data-note').textContent.includes('expired'));
fail=false;vm.runInContext('connectGoogle()',ctx);grant=false;await callback({access_token:'fixture-access-token',expires_in:3600});assert.ok(elements.get('data-note').textContent.includes('grant'));assert.equal(elements.get('account-email').textContent,'Not signed in');
grant=true;vm.runInContext('connectGoogle()',ctx);await callback({access_token:'fixture-access-token',expires_in:3600});vm.runInContext('disconnectGoogle()',ctx);assert.ok(!elements.get('issue-rows').innerHTML.includes('Fixture issue'));
console.log('Passed fixture browser flow: consent, live mapping, no invented progress, stale network handling, token expiry, missing scopes, and disconnect.');})().catch(e=>{console.error(e);process.exitCode=1});

}

require('./session.cjs');


require('./ai-review.cjs');


require('./uat.cjs');


require('./table-sort.cjs');

require('./koha-screenshots.cjs');
