(function(){
'use strict';
const base=new URL('.',document.currentScript.src),listeners=new Set();
let worker,current=null;
try{worker=new SharedWorker(new URL('session-worker.js',base),{name:'somaiya-dashboard-session-v1'});worker.port.onmessage=({data})=>{if(data?.type!=='session')return;current=data.session;for(const listener of listeners)listener(current);};worker.port.start();worker.port.postMessage({type:'get'});}catch{worker=null;}
window.SomaiyaSession={
 available:!!worker,
 subscribe(listener){listeners.add(listener);if(current)listener(current);return()=>listeners.delete(listener);},
 set(session){if(worker)worker.port.postMessage({type:'set',session});},
 clear(reason){if(worker)worker.port.postMessage({type:'clear',reason});}
};
window.addEventListener('pagehide',event=>{if(!event.persisted&&worker)worker.port.postMessage({type:'close'});});
})();
