(function(){
'use strict';
const SHEET_ID='1Slj1cNkj4ZKE1-lXc1Ci-wwaibIEu0-lcRNnxkb7keU';
const REQUESTED_TAB='All modules Bugs, Issues';
const normalize=s=>String(s||'').trim().toLowerCase().replace(/\s+/g,' ');
function parseDate(value) {
  const s = String(value || '').trim();
  if (!s) return '';
  let result = s;
  const match = s.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/);
  if (match) result = `${match[3]}-${match[2].padStart(2,'0')}-${match[1].padStart(2,'0')}`;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result)) return '';
  const date = new Date(result + 'T12:00:00Z');
  return !isNaN(date) && date.toISOString().slice(0,10) === result ? result : '';
}
function selectTab(sheets) {
  // User's spelling differs slightly from the screenshot; accept only these two names.
  const allowed = [normalize(REQUESTED_TAB), normalize('All Module Bugs, Issues')];
  const matches = sheets.map(s=>s.properties).filter(p=>allowed.includes(normalize(p.title)));
  const exact = matches.find(p=>normalize(p.title)===normalize(REQUESTED_TAB));
  if (exact) return exact;
  if (matches.length === 1) return matches[0];
  throw Error('The selected All modules Bugs, Issues tab was not found. Confirm its exact name.');
}
function mapRows(values, tab) {
  const headerIndex = values.findIndex(row=>row.some(c=>normalize(c)==='description'));
  if (headerIndex < 0) throw Error('The selected tab needs a Description column.');
  const headings = values[headerIndex].map(normalize);
  const get = (row,...names) => {
    for (const name of names) { const i=headings.indexOf(name); if(i>=0)return String(row[i]??'').trim(); }
    return '';
  };
  const has = (...names) => names.some(name=>headings.includes(name));
  const warnings = [];
  let missingReported=0, missingResolved=0, invalidDates=0, unknownStatus=0, invalidProgress=0;
  const ids = new Set(); let duplicateIds=0, missingIds=0;
  const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const issues = values.slice(headerIndex+1).flatMap((row,index)=>{
    const description=get(row,'description'); if(!description)return [];
    const rowNumber=headerIndex+index+2;
    const id=get(row,'issue id','id','ticket id')||`ROW-${rowNumber}`;
    if(!get(row,'issue id','id','ticket id'))missingIds++;
    if(ids.has(id))duplicateIds++;ids.add(id);
    const rawStatus=get(row,'status');
    const statusMap={'completed':'Resolved','resolved':'Resolved','closed':'Resolved','pending':'Pending','open':'Pending','in progress':'In Progress','blocked':'Blocked'};
    const status=statusMap[normalize(rawStatus)]||rawStatus||'Pending';
    if(rawStatus&&!statusMap[normalize(rawStatus)])unknownStatus++;
    const readDate=(...names)=>{const raw=get(row,...names),date=parseDate(raw);if(raw&&!date)invalidDates++;return date};
    let reported=readDate('date reported','reported date','reported');
    const eta=readDate('eta','target resolution date','target date','due date');
    let resolved=status==='Resolved'?readDate('resolution date','resolved date','date resolved','date completed'):'';
    if(reported>today){reported='';invalidDates++}
    if(resolved&&(resolved>today||reported&&resolved<reported)){resolved='';invalidDates++}
    if(!reported)missingReported++;
    if(status==='Resolved'&&!resolved)missingResolved++;
    const rawProgress=get(row,'progress','progress (%)','progress %');
    let progress=null;
    if(rawProgress){const numeric=Number(rawProgress.replace(/%$/, '').trim());if(Number.isFinite(numeric)&&numeric>=0&&numeric<=100)progress=numeric;else invalidProgress++}
    const rawPriority=get(row,'priority');
    const priorities={'super critical':'Super Critical','urgent':'Urgent','high':'High','normal':'Normal'};
    return [{id,key:`${tab.sheetId}:${rowNumber}`,description,module:get(row,'module')||'Unspecified',status,type:get(row,'type')||'Unspecified',priority:priorities[normalize(rawPriority)]||rawPriority||'Unspecified',owner:get(row,'assigned to','owner'),progress,reported,eta,resolved,blocker:get(row,'blocker','next action'),notes:get(row,'notes','resolution notes','remarks'),updated:get(row,'last updated','updated'),sourceUrl:`https://docs.google.com/spreadsheets/d/${SHEET_ID}/edit#gid=${tab.sheetId}&range=A${rowNumber}`}];
  });
  if(!has('module'))warnings.push('Module column is missing.');
  if(!has('status'))warnings.push('Status column is missing; all rows are treated as pending.');
  if(!has('resolution date','resolved date','date resolved','date completed'))warnings.push('Add a Resolution Date column to count daily closures.');
  if(missingReported)warnings.push(`${missingReported} issues have no valid reported date and are excluded from historical pending counts.`);
  if(missingResolved)warnings.push(`${missingResolved} resolved issues have no valid resolution date and are excluded from daily closures and historical pending counts.`);
  if(invalidDates)warnings.push(`${invalidDates} invalid dates were ignored.`);
  if(unknownStatus)warnings.push(`${unknownStatus} unrecognized statuses are treated as unresolved.`);
  if(invalidProgress)warnings.push(`${invalidProgress} invalid progress values were ignored.`);
  if(missingIds)warnings.push(`${missingIds} issues use row references; add Issue ID to preserve references when rows move.`);
  if(duplicateIds)warnings.push(`${duplicateIds} duplicate Issue IDs found; details use unique row references.`);
  return {issues,warnings};
}

window.SomaiyaSheet=Object.freeze({parseDate,selectTab,mapRows,SHEET_ID,REQUESTED_TAB});
})();