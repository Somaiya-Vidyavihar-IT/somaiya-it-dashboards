(function(){
'use strict';
// The portal remains the session owner when an embedded dashboard changes.
if(window.parent!==window&&window.parent.location.origin===window.location.origin&&window.parent.SomaiyaSession){window.SomaiyaSession=window.parent.SomaiyaSession;return;}
const base=new URL('.',document.currentScript.src),listeners=new Set();
// Scope the channel to this deployment's path so /dev/ preview tabs never share or clear the live site's session.
const sessionName='somaiya-dashboard-session-v1:'+base.pathname;
let worker,current=null,channel,stamp=0,expiryTimer;
const active=s=>!!s?.accessToken&&s.expiresAt>Date.now();
function notify(){clearTimeout(expiryTimer);if(active(current))expiryTimer=setTimeout(()=>window.SomaiyaSession.clear('Google authorization expired. Connect with Google again.'),Math.min(current.expiresAt-Date.now(),2147483647));for(const listener of listeners)listener(current);}
function announce(){channel?.postMessage({type:'state',session:current,stamp});}
function adopt(session){if(current&&current.accessToken===session.accessToken&&current.email===session.email&&current.expiresAt===session.expiresAt&&current.reason===session.reason)return;current=session;stamp=Math.max(Date.now(),stamp+1);notify();announce();}
try{channel=new BroadcastChannel(sessionName);channel.onmessage=({data})=>{if(data?.type==='request'){if(current&&(active(current)||current.reason))announce();return;}if(data?.type!=='state'||!data.session||!Number.isFinite(data.stamp)||data.stamp<=stamp)return;if(data.session.accessToken&&!active(data.session))return;current=data.session;stamp=data.stamp;notify();if(worker&&active(current))worker.port.postMessage({type:'set',session:current});};channel.postMessage({type:'request'});}catch{channel=null;}
try{worker=new SharedWorker(new URL('session-worker.js',base),{name:sessionName});worker.port.onmessage=({data})=>{if(data?.type!=='session')return;if(!data.session.accessToken&&!data.session.reason){if(active(current))worker.port.postMessage({type:'set',session:current});return;}adopt(data.session);};worker.port.start();worker.port.postMessage({type:'get'});}catch{worker=null;}
window.SomaiyaSession={
 available:!!worker||!!channel,
 subscribe(listener){listeners.add(listener);if(current)listener(current);return()=>listeners.delete(listener);},
 set(session){adopt({...session,reason:''});if(worker)worker.port.postMessage({type:'set',session});},
 clear(reason='Disconnected across all dashboards.'){adopt({accessToken:'',expiresAt:0,email:'',reason});if(worker)worker.port.postMessage({type:'clear',reason});}
};
window.addEventListener('pagehide',event=>{if(!event.persisted&&worker)worker.port.postMessage({type:'close'});});
})();
