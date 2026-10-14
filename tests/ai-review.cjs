const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..'),review='Diagnostic phrase\n• Suggested next step\n<script>alert(1)</script>';
for(const name of ['production-support','koha-support']){
 const html=fs.readFileSync(path.join(root,'site',name,'index.html'),'utf8');
 const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',innerHTML:'',open:false,classList:{toggle(){},add(){},remove(){}},querySelectorAll(){return[]},addEventListener(){},showModal(){this.open=true},close(){this.open=false}}]));
 const ctx={window:{location:{origin:'https://fixture.example'},addEventListener(){}},Intl,Date,Set,URL,Blob,AbortSignal,setInterval(){},setTimeout(){},clearTimeout(){},document:{hidden:false,getElementById(id){assert.ok(elements.has(id),id);return elements.get(id)},querySelectorAll(){return[]},addEventListener(){}}};
 vm.createContext(ctx);if(name==='production-support'){vm.runInContext(fs.readFileSync(path.join(root,'site/shared/sheet-data.js'),'utf8'),ctx);ctx.SomaiyaSheet=ctx.window.SomaiyaSheet;}
 vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
 const values=name==='production-support'?[['Description','Status','Date Reported','AI Review'],['First','Pending','13-10-2026',review],['Second','Pending','13-10-2026','   '],['Third','Resolved','13-10-2026','Resolved analysis']]:[['Timestamp','Brief Description of the Issue','Status','AI Review'],[46308,'First','Assigned',review],[46308,'Second','Assigned','   '],[46308,'Third','Resolved','Resolved analysis']];
 ctx.values=values;vm.runInContext("issues=SomaiyaSheet.mapRows(values,{title:'Fixture',sheetId:1}).issues;sample=false;render()",ctx);
 assert.equal(vm.runInContext('issues[0].aiReview',ctx),review);assert.equal(vm.runInContext('issues[1].aiReview',ctx),'');
 assert.match(elements.get('review-coverage').textContent,/1 of 2 pending issues/);
 assert.ok(elements.get('issue-rows').innerHTML.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));assert.ok(!elements.get('issue-rows').innerHTML.includes('<script>'));
 elements.get('filter-review').value='available';vm.runInContext('renderTable()',ctx);assert.ok(elements.get('issue-rows').innerHTML.includes('First'));assert.ok(!elements.get('issue-rows').innerHTML.includes('Second'));
 elements.get('filter-review').value='missing';vm.runInContext('renderTable()',ctx);assert.ok(elements.get('issue-rows').innerHTML.includes('Second'));assert.ok(!elements.get('issue-rows').innerHTML.includes('First'));
 elements.get('filter-review').value='';elements.get('search').value='diagnostic phrase';vm.runInContext('renderTable()',ctx);assert.match(elements.get('row-count').textContent,/Showing 1 of 3/);
 vm.runInContext('showIssue(issues[0].key)',ctx);assert.ok(elements.get('detail-content').innerHTML.includes('AI Review'));assert.ok(elements.get('detail-content').innerHTML.includes('&lt;script&gt;'));
 vm.runInContext("issues[0].aiReview='Updated review from refresh';render()",ctx);assert.ok(elements.get('detail-content').innerHTML.includes('Updated review from refresh'));
 assert.ok(elements.get('pending-review-list').innerHTML.includes('No AI review available'));
 vm.runInContext("download=(name,text)=>{window.captured={name,text}}",ctx);elements.get('export').onclick();assert.ok(ctx.window.captured.text.includes('AI Review'));assert.ok(ctx.window.captured.text.includes('Updated review from refresh'));
 elements.get('report-download').onclick();assert.ok(ctx.window.captured.text.includes('AI Review (current): Updated review from refresh'));assert.ok(ctx.window.captured.text.includes('historical review versions are not recorded'));
}
console.log('Passed AI Review: both mappings, blank reviews, text escaping, filters/search, pending coverage, refreshed details, CSV and daily report exports.');
