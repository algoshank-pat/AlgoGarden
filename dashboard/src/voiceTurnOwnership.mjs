// Half duplex: only the customer turn owns the microphone. No barge-in capture.
export class VoiceTurnOwnership {
 constructor({onReset=()=>{}}={}){this.owner='off';this.manualMuted=false;this.stream=null;this.onReset=onReset}
 attach(stream){this.stream=stream;this.sync()}
 setOwner(owner){this.owner=owner;this.onReset(owner);this.sync()}
 setManualMuted(value){this.manualMuted=value;this.onReset('muted');this.sync()}
 get canCapture(){return this.owner==='customer'&&!this.manualMuted}
 sync(){this.stream?.getAudioTracks().forEach(track=>{track.enabled=this.canCapture})}
}

// Only numeric, correlated timing data is sent; never transcripts or audio.
export async function submitTurnTimings(sessionId,requestId,metrics,{fetcher=globalThis.fetch.bind(globalThis),onError=()=>{}}={}){
 const outcomes=await Promise.all(Object.entries(metrics).map(async([stage,duration_ms])=>{
  if(!Number.isFinite(duration_ms)||duration_ms<0){onError({session_id:sessionId,request_id:requestId,stage,status:'invalid_metric'});return false}
  try{
   const response=await fetcher(`/api/sessions/${sessionId}/timings`,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,stage,duration_ms})});
   if(!response.ok)throw Object.assign(new Error('Timing submission failed'),{status:response.status});
   return true;
  }catch(error){onError({session_id:sessionId,request_id:requestId,stage,status:error.status||'network'});return false}
 }));
 return outcomes.every(Boolean);
}
