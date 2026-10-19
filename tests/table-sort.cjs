// Fixture-only checks for queue sorting, hub accessibility markup, and the shared release token.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(root,f),'utf8');

function th(text){const el={textContent:text,attrs:{},children:[],setAttribute(k,v){this.attrs[k]=String(v)},appendChild(c){this.children.push(c)}};return el;}
const heads=[th('Issue'),th('Reported'),th('Progress')];
const document={createElement(){return {type:'',className:'',textContent:'',listeners:{},addEventListener(e,fn){this.listeners[e]=fn}}}};
const ctx={window:{},document};vm.createContext(ctx);vm.runInContext(read('site/shared/table-sort.js'),ctx);
let changes=0;
const sorter=ctx.window.SomaiyaTableSort.init({querySelectorAll:()=>heads},{columns:[i=>i.title,i=>i.reported,null],onChange:()=>changes++});
const rows=[{title:'b',reported:'2026-10-02'},{title:'A',reported:''},{title:'c',reported:'2026-10-01'}];

assert.equal(heads[0].children[0].type,'button');assert.equal(heads[0].children[0].textContent,'Issue');
assert.equal(heads[0].attrs['aria-sort'],'none');assert.equal(heads[2].children.length,0,'Unsortable columns stay plain headers');
assert.deepEqual(sorter.apply(rows),rows,'Default order is unchanged until a header is chosen');

heads[1].children[0].listeners.click();
assert.equal(changes,1);assert.equal(heads[1].attrs['aria-sort'],'ascending');assert.equal(heads[0].attrs['aria-sort'],'none');
assert.deepEqual(sorter.apply(rows).map(r=>r.title),['c','b','A'],'Missing dates sort last');
heads[1].children[0].listeners.click();
assert.equal(heads[1].attrs['aria-sort'],'descending');
assert.deepEqual(sorter.apply(rows).map(r=>r.title),['b','c','A'],'Missing dates stay last when descending');
heads[0].children[0].listeners.click();
assert.deepEqual(sorter.apply(rows).map(r=>r.title),['A','b','c'],'Text sorts case-insensitively');
assert.equal(rows[0].title,'b','Sorting does not mutate source rows');

const pages=['production-support','koha-support','uat-observations'].map(p=>read(`site/${p}/index.html`));
for(const html of pages){assert.ok(html.includes('<table id="issue-table">'));assert.ok(html.includes('shared/table-sort.js?v='));assert.ok(html.includes('sortQueue('));}

const hub=read('site/index.html');
assert.ok(hub.includes('class="skip-link"'));assert.ok(hub.includes('role="status" aria-live="polite"'));
assert.equal((hub.match(/aria-label="Open [^"]+ dashboard"/g)||[]).length,3);
assert.ok(!/<button(?![^>]*type="button")/.test(hub),'Hub buttons declare type="button"');

const tokens=new Set();
for(const html of [hub,...pages]){for(const m of html.matchAll(/\.js\?v=([^"']+)/g))tokens.add(m[1]);for(const m of html.matchAll(/searchParams\.set\('v','([^']+)'\)/g))tokens.add(m[1]);}
assert.equal(tokens.size,1,'One release token across hub, pages, and shared scripts: '+[...tokens].join(', '));

console.log('Passed table sorting: accessible headers, aria-sort, blanks last, no mutation; hub accessibility markup; single release token.');
