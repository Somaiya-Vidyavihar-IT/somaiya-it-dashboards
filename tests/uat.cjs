const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/uat-observations/index.html'),'utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1];new Function(code);
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',innerHTML:'',open:false,classList:{toggle(){},add(){},remove(){}},addEventListener(){},showModal(){this.open=true},close(){this.open=false}}]));
let callback,subscriber,failed=0,grant=true,tokenRequests=0,requests=[],clears=0;
const values=[['Sr No.','Date Marked','Observations','Status','Remarks','Remarks from Somaiya IT/ID Office team'],
 ['1','11/09/2026','Door <script>alert(1)</script>','Done','Vendor done','Pending acceptance'],
 ['2','','Fixture second','Working as expected','Expected','Check again'],
 ['3','','=Untriaged','','=formula','Team\nline two'],
 ['4','','Unknown-status observation','Data issue','',''],
 ['5','31/02/2026','Clarification','Need clarifications\n@fixture.example','',''],
 ['6','15/09/2026','Previously done','Already Done','',''],
 ['7','','','Done','Note without observation','']];
const sheets=[{properties:{title:'Main Tracker',sheetId:1}},{properties:{title:'UAT Obervations',sheetId:1902995312},merges:[{startColumnIndex:1,endColumnIndex:2,startRowIndex:1,endRowIndex:4}]}];
const oauth={initTokenClient(o){callback=o.callback;return {requestAccessToken(){tokenRequests++}}},hasGrantedAllScopes(){return grant}};
const window={location:{origin:'http://127.0.0.1:8765'},SOMAIYA_CONFIG:{googleClientId:'fixture.apps.googleusercontent.com'},google:{accounts:{oauth2:oauth}},addEventListener(){},SomaiyaSession:{subscribe(fn){subscriber=fn},set(){},clear(){clears++}}};
const ctx={console,window,google:window.google,Intl,Date,URL,Blob,AbortSignal,setInterval(){},setTimeout(){},clearTimeout(){},document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){}},fetch:async(url,opts)=>{requests.push(url);assert.equal(opts.cache,'no-store');assert.ok(opts.headers.Authorization.startsWith('Bearer '));if(failed)return {ok:false,status:failed};return {ok:true,json:async()=>url.includes('userinfo')?{email:'fixture@example.com'}:url.includes('/values/')?{values}:{sheets}}}};
vm.createContext(ctx);vm.runInContext(code,ctx);
const run=s=>vm.runInContext(s,ctx);
assert.equal(run("SomaiyaSheet.parseDate('11/09/2026')"),'2026-09-11');
assert.equal(run("SomaiyaSheet.parseDate('31/02/2026')"),'');
assert.equal(run('SomaiyaSheet.parseDate(46308)'),'2026-10-13');
assert.throws(()=>run("SomaiyaSheet.selectTab([{properties:{title:'Main Tracker'}}])"));
ctx.fixtureValues=values;ctx.fixtureSheets=sheets;
assert.equal(run('SomaiyaSheet.mapRows(fixtureValues,SomaiyaSheet.selectTab(fixtureSheets)).issues.length'),6);
const map=run('SomaiyaSheet.mapRows(fixtureValues,SomaiyaSheet.selectTab(fixtureSheets))');
assert.equal(map.issues[1].reported,'2026-09-11');assert.equal(map.issues[2].mergedDate,true);
assert.equal(map.issues[3].reported,'');assert.equal(map.issues[4].reported,'');
assert.equal(map.issues.filter(i=>i.complete).length,3);assert.equal(map.issues[0].group,'done');assert.equal(map.issues[1].group,'expected');assert.equal(map.issues[2].group,'missing');
assert.equal(map.issues[0].team,'Pending acceptance');assert.ok(map.issues[0].complete,'Remarks must not override official status');
assert.ok(map.warnings.some(w=>w.includes('excluded')));assert.ok(map.issues[0].sourceUrl.includes('gid=1902995312'));
// Reordered fields and merges follow actual columns, not fixed B.
ctx.reordered=[['Status','Observations','Date Marked','Sr No.'],['Done','First','12/09/2026','A'],['','Second','','A'],['','Third','','C']];
const reordered=run('SomaiyaSheet.mapRows(reordered,{sheetId:2,merges:[{startColumnIndex:2,endColumnIndex:3,startRowIndex:1,endRowIndex:3}]})');
assert.equal(reordered.issues[1].reported,'2026-09-12');assert.equal(reordered.issues[2].reported,'');assert.ok(reordered.warnings.some(w=>w.includes('duplicate')));
assert.ok(run("csvCell(' =IMPORTXML(\"x\")')").startsWith('"\''));
assert.ok(!html.includes('localStorage'));assert.ok(!html.includes('sessionStorage'));assert.ok(!html.includes('client_secret'));
assert.ok(html.includes("sheets(properties(sheetId,title),merges)"));
(async()=>{
 run('initializeGoogle();connectGoogle()');assert.equal(tokenRequests,1);await callback({access_token:'fixture-access-token',expires_in:3600});
 assert.equal(elements.get('account-email').textContent,'fixture@example.com');
 assert.ok(requests.some(u=>u.includes('/values/')&&decodeURIComponent(u).includes("'UAT Obervations'")));
 assert.ok(!requests.some(u=>u.includes('/values/')&&u.includes('Main')));
 assert.equal(run('issues.length'),6);assert.equal(run('issues.filter(open).length'),3);
 run("tab='all';renderTable()");assert.ok(elements.get('issue-rows').innerHTML.includes('&lt;script&gt;'));assert.ok(!elements.get('issue-rows').innerHTML.includes('<script>'));
 elements.get('search').value='Pending acceptance';run('renderTable()');assert.equal(run('filtered().length'),1);
 elements.get('search').value='';elements.get('filter-remarks').value='missing';assert.equal(run('filtered().length'),3);
 elements.get('filter-remarks').value='';elements.get('date-from').value='2026-09-12';assert.equal(run('filtered().length'),1);
 elements.get('date-from').value='';run("tab='missing';renderTable()");assert.equal(run('filtered().length'),1);
 run("showIssue('row-2')");assert.ok(elements.get('details').open);assert.ok(elements.get('detail-body').innerHTML.includes('Pending acceptance'));
 elements.get('report-date').value='2026-09-11';run('renderReport()');assert.equal(run("snapshot('2026-09-11').marked.length"),3);assert.ok(run('reportText()').includes('unavailable (no closure date)'));
 assert.ok(run('csvText(issues)').includes("'=formula"));assert.ok(run('csvText(issues)').includes('Team\nline two'));
 failed=429;await run('refreshSheet()');assert.ok(elements.get('data-note').textContent.includes('stale'));assert.equal(run('issues.length'),6);
 failed=403;await run('refreshSheet()');assert.equal(run('issues.length'),0);assert.equal(run('authState.authenticated'),true);assert.equal(clears,0);
 failed=0;await run('refreshSheet()');assert.equal(run('issues.length'),6);
 await subscriber({accessToken:'',expiresAt:0,reason:'Fixture global logout'});assert.equal(run('issues.length'),0);assert.equal(elements.get('details').open,false);assert.ok(!elements.get('detail-body').innerHTML.includes('fixture-access-token'));
 // Shared session handoff fetches the UAT tab without another Google popup.
 await subscriber({accessToken:'fixture-shared-token',expiresAt:Date.now()+3600000,email:'shared@example.com'});
 await new Promise(r=>setImmediate(r));assert.equal(elements.get('account-email').textContent,'shared@example.com');assert.equal(run('issues.length'),6);assert.equal(tokenRequests,1);
 run('tokenExpiresAt=0');await run('refreshSheet()');assert.equal(run('issues.length'),0);assert.equal(clears,1);
 console.log('Passed UAT fixtures: selected tab, merged/reordered dates, blank and unknown statuses, separate remarks, filters, escaped details, CSV, snapshots, stale/access errors, shared sign-in, logout and expiry.');
})().catch(e=>{console.error(e);process.exitCode=1});
