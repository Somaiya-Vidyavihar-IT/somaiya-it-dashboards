// Accessible column sorting for dashboard queue tables. Sorting is display-only:
// it reorders already filtered rows and never changes sheet values.
(function(){
const cmp=(a,b)=>typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b),'en',{numeric:true,sensitivity:'base'});
const blank=v=>v==null||v===''||(typeof v==='number'&&!Number.isFinite(v));
function init(table,{columns,onChange}){
  const heads=[...table.querySelectorAll('thead th')],state={index:-1,dir:'ascending'};
  const update=()=>heads.forEach((th,i)=>{if(columns[i])th.setAttribute('aria-sort',i===state.index?state.dir:'none');});
  heads.forEach((th,i)=>{
    if(!columns[i])return;
    const button=document.createElement('button');button.type='button';button.className='sort-button';button.textContent=th.textContent;
    th.textContent='';th.appendChild(button);
    button.addEventListener('click',()=>{state.dir=state.index===i&&state.dir==='ascending'?'descending':'ascending';state.index=i;update();onChange();});
  });
  update();
  return {
    // Blank values (missing dates, unassigned progress) stay last in either direction.
    apply(list){
      if(state.index<0)return list;
      const get=columns[state.index],sign=state.dir==='ascending'?1:-1;
      return list.map((item,i)=>[item,i,get(item)]).sort((a,b)=>{const ea=blank(a[2]),eb=blank(b[2]);if(ea||eb)return ea&&eb?a[1]-b[1]:ea?1:-1;return sign*cmp(a[2],b[2])||a[1]-b[1];}).map(x=>x[0]);
    },
    state:()=>({...state}),
    reset(){state.index=-1;state.dir='ascending';update();}
  };
}
window.SomaiyaTableSort={init};
})();
