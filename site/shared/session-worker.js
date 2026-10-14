'use strict';
// Authorization lives only in shared browser memory, never in persistent storage.
const ports=new Set();
let session={accessToken:'',expiresAt:0,email:'',reason:'',revision:0},expiryTimer;
function emit(port){try{port.postMessage({type:'session',session});}catch{ports.delete(port);}}
function broadcast(){for(const port of ports)emit(port);}
function clear(reason='Disconnected across all dashboards.'){clearTimeout(expiryTimer);session={accessToken:'',expiresAt:0,email:'',reason,revision:session.revision+1};broadcast();}
function validate(){if(session.accessToken&&Date.now()>=session.expiresAt)clear('Google authorization expired. Connect with Google again.');}
onconnect=event=>{const port=event.ports[0];ports.add(port);port.onmessage=({data})=>{
 if(data?.type==='get'){validate();emit(port);}
 if(data?.type==='set'){
  const s=data.session;if(!s||typeof s.accessToken!=='string'||!s.accessToken||typeof s.email!=='string'||!s.email||!Number.isFinite(s.expiresAt)||s.expiresAt<=Date.now())return;
  clearTimeout(expiryTimer);session={accessToken:s.accessToken,expiresAt:s.expiresAt,email:s.email,reason:'',revision:session.revision+1};
  expiryTimer=setTimeout(()=>clear('Google authorization expired. Connect with Google again.'),Math.min(s.expiresAt-Date.now(),2147483647));broadcast();
 }
 if(data?.type==='clear')clear(typeof data.reason==='string'?data.reason:undefined);
 if(data?.type==='close'){ports.delete(port);port.close();}
};port.start();validate();emit(port);};
