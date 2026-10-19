// Fixture-only checks for KOHA screenshot links; these do not call Google or Drive.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/koha-support/index.html'),'utf8');
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',innerHTML:'',open:false,href:'',classList:{toggle(){},add(){},remove(){}},querySelectorAll(){return[]},addEventListener(){},showModal(){this.open=true},close(){this.open=false}}]));
const window={location:{origin:'http://127.0.0.1:8765'},SOMAIYA_CONFIG:{googleClientId:'fixture.apps.googleusercontent.com'},addEventListener(){}};
const ctx={console,window,Intl,Date,URL,Blob,AbortSignal,setInterval(){},setTimeout(){},clearTimeout(){},document:{hidden:false,getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){}},fetch:async()=>{throw Error('No network in fixtures')}};
vm.createContext(ctx);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
const run=s=>vm.runInContext(s,ctx),plain=x=>JSON.parse(JSON.stringify(x));

const parse=v=>run('SomaiyaSheet.parseScreenshots')(v);
const one=parse('https://drive.google.com/open?id=1jP6mOynnYc9VD-hldguXMi5abc');
assert.equal(one.shots.length,1);assert.equal(one.shots[0].driveId,'1jP6mOynnYc9VD-hldguXMi5abc');assert.equal(one.invalid,0);
const many=parse('https://drive.google.com/open?id=AAAAAAAAAAAA, https://drive.google.com/file/d/BBBBBBBBBBBB/view\nhttps://example.com/pic.png');
assert.deepEqual(plain(many.shots.map(s=>s.driveId)),['AAAAAAAAAAAA','BBBBBBBBBBBB',''],'Comma/newline separated links; non-Drive links get no preview');
const bad=parse('javascript:alert(1) http://drive.google.com/open?id=CCCCCCCCCCCC not-a-link https://drive.google.com/open?id=bad"id<x>');
assert.equal(bad.shots.length,1,'Only https links are kept');assert.equal(bad.shots[0].driveId,'','Unsafe Drive IDs never become previews');assert.equal(bad.invalid,3);
assert.deepEqual(plain(parse('').shots),[]);assert.deepEqual(plain(parse(null).shots),[]);

const values=[['Timestamp','Case ID','Status','Brief Description of the Issue','Upload Screenshot (if available)'],
 [46300,'K-1','Open','With screenshots','https://drive.google.com/open?id=AAAAAAAAAAAA, https://drive.google.com/open?id=BBBBBBBBBBBB'],
 [46300,'K-2','Open','No screenshot',''],
 [46300,'K-3','Open','Unsafe link','javascript:alert(1)']];
const mapped=run('SomaiyaSheet.mapRows')(values,{sheetId:7,title:'Form Responses 1'});
assert.equal(mapped.issues[0].screenshots.length,2);assert.equal(mapped.issues[1].screenshots.length,0);assert.equal(mapped.issues[2].screenshots.length,0);
assert.ok(mapped.warnings.some(w=>w.includes('1 screenshot entries are not https links')));
const noColumn=run('SomaiyaSheet.mapRows')([values[0].slice(0,4),values[1].slice(0,4)],{sheetId:7});
assert.deepEqual(plain(noColumn.issues[0].screenshots),[],'Missing column is optional');assert.ok(!noColumn.warnings.some(w=>w.includes('screenshot')));

ctx.fixtureIssues=mapped.issues;run('issues=fixtureIssues;sample=false;renderTable();showIssue("row-2")');
const detail=elements.get('detail-content').innerHTML;
assert.equal((detail.match(/class="shot-img"/g)||[]).length,2);
assert.ok(detail.includes('src="https://drive.google.com/thumbnail?id=AAAAAAAAAAAA&amp;sz=w800"'));
assert.ok(detail.includes('href="https://drive.google.com/file/d/BBBBBBBBBBBB/view" target="_blank" rel="noopener noreferrer"'));
assert.ok(detail.includes('referrerpolicy="no-referrer"'));assert.ok(detail.includes('alt="Screenshot 1 for K-1"'));
assert.ok(elements.get('issue-rows').innerHTML.includes('2 screenshots'));
run('showIssue("row-3")');assert.ok(elements.get('detail-content').innerHTML.includes('No screenshot supplied'));
run('showIssue("row-4")');assert.ok(!elements.get('detail-content').innerHTML.includes('javascript:'),'Rejected links are never rendered');

run('download=(name,text)=>{globalThis.fixtureExport=text};document.getElementById("export").onclick()');const exported=ctx.fixtureExport;
assert.ok(exported.split('\r\n')[0].endsWith('"Screenshots"'));
assert.ok(exported.includes('"https://drive.google.com/open?id=AAAAAAAAAAAA https://drive.google.com/open?id=BBBBBBBBBBBB"'));

console.log('Passed KOHA screenshots: optional column, https/Drive validation, multiple links, escaped previews with fallback links, queue indicator, CSV column.');
